"""Focused unit + smoke tests around recovery, guards, parsing, and locking."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

# Force rules mode + high chaos so tests don't hit the network
os.environ.setdefault("MODE", "rules")
os.environ.setdefault("CHAOS", "0.0")
os.environ.setdefault("SEED", "7")

from app.config import CONFIG  # noqa: E402
from app.models import Classification, Failure, Reply, Ticket  # noqa: E402
from app.policies import (  # noqa: E402
    compute_action_confidence,
    compute_decision_basis,
    compute_evidence_confidence,
    extract_order_id,
    refund_guard,
)
from app.registry import RegistryError, call_tool  # noqa: E402
from app.state import TicketState  # noqa: E402
from app.tools import (  # noqa: E402
    TOOL_REGISTRY,
    _IDEMPOTENCY,
    _ORDER_LOCKS,
    _idempotency_key,
    issue_refund,
    send_reply,
    cancel_order,
    initiate_exchange,
)


def _make_state(ticket_id: str = "TKT-TEST") -> TicketState:
    t = Ticket(
        ticket_id=ticket_id,
        customer_email="alice.turner@email.com",
        subject="test",
        body="test",
        source="email",
        created_at="2024-03-15T00:00:00Z",
    )
    return TicketState(ticket=t)


@pytest.mark.asyncio
async def test_timeout_recovery(monkeypatch):
    """get_order fails once with a timeout, retries, then succeeds."""
    calls = {"n": 0}
    real = TOOL_REGISTRY["get_order"]

    async def flaky(ctx, order_id):
        calls["n"] += 1
        if ctx["attempt"] == 0:
            raise TimeoutError("simulated")
        return await real(ctx, order_id=order_id)

    monkeypatch.setitem(TOOL_REGISTRY, "get_order", flaky)
    state = _make_state()
    result = await call_tool("get_order", state, order_id="ORD-1001")
    assert result["found"] is True
    assert state.recovery_attempted is True
    assert any(f.tool == "get_order" and f.recovered for f in state.failures)
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_refund_guard_blocks_without_eligibility():
    state = _make_state()
    # no check_refund_eligibility in tools_used
    ok, reason = refund_guard(state)
    assert ok is False
    assert "eligibility" in reason.lower()

    # add eligibility with negative result
    state.tools_used.append("check_refund_eligibility")
    state.cache["eligibility"] = {"eligible": False, "reason": "window expired"}
    ok, reason = refund_guard(state)
    assert ok is False

    # positive eligibility unblocks
    state.cache["eligibility"] = {"eligible": True, "reason": "ok"}
    ok, _ = refund_guard(state)
    assert ok is True

    # DOA still requires the eligibility tool to have run first
    state = _make_state()
    state.category = "damaged_on_arrival"
    state.cache["order"] = {"found": True, "order_id": "ORD-1008", "amount": 89.0}
    ok, reason = refund_guard(state)
    assert ok is False
    assert "eligibility" in reason.lower()


def test_extract_order_id_normalizes_common_variants():
    assert extract_order_id("Order ORD-1001") == "ORD-1001"
    assert extract_order_id("Order ord1001") == "ORD-1001"
    assert extract_order_id("Order ORD 1001") == "ORD-1001"
    assert extract_order_id("Order ord-123456 is delayed") == "ORD-123456"


@pytest.mark.asyncio
async def test_issue_refund_lock_is_cleaned_up_after_contention():
    order_id = "ORD-1001"
    _IDEMPOTENCY.clear()
    _ORDER_LOCKS.clear()

    async def one_call(i: int):
        return await issue_refund(
            {"ticket_id": f"TKT-LOCK-{i}", "attempt": 0},
            order_id=order_id,
            amount=129.99,
        )

    results = await asyncio.gather(*(one_call(i) for i in range(10)))
    assert sum(1 for r in results if r.get("issued")) == 1
    assert sum(1 for r in results if not r.get("issued")) == 9
    assert order_id not in _ORDER_LOCKS


def test_compute_decision_basis_all_paths():
    # 1. unresolvable
    s = _make_state()
    s.outcome = "escalated"
    assert compute_decision_basis(s) == "unresolvable_ticket"

    # 2. tool failure
    s = _make_state()
    s.failures.append(Failure(tool="get_order", error="timeout", recovered=False))
    assert compute_decision_basis(s) == "tool_failure"

    # 3. fraud
    s = _make_state()
    s.cache["fraud_flag"] = "claimed premium but standard"
    assert compute_decision_basis(s) == "fraud_detected"

    # 4. low confidence on an escalated ticket
    s = _make_state()
    s.confidence = 0.5
    s.tools_used.append("get_order")
    s.outcome = "escalated"
    assert compute_decision_basis(s) == "low_confidence"

    # 5. policy guard (irreversible + below refund floor, escalated)
    s = _make_state()
    s.tools_used.append("get_order")
    s.confidence = 0.8
    s.intends_irreversible = True
    s.outcome = "escalated"
    assert compute_decision_basis(s) == "policy_guard"

    # 6. recovered and resolved
    s = _make_state()
    s.tools_used.append("get_order")
    s.confidence = 0.95
    s.recovery_attempted = True
    s.outcome = "resolved"
    assert compute_decision_basis(s) == "recovered_and_resolved"

    # 7. successful resolution (happy path)
    s = _make_state()
    s.tools_used.append("get_order")
    s.confidence = 0.95
    s.outcome = "resolved"
    assert compute_decision_basis(s) == "successful_resolution"


def test_confidence_split_promotes_strong_verified_evidence():
    s = _make_state()
    s.category = "refund_request"
    s.intends_irreversible = True
    s.cache["customer"] = {"customer_id": "C001", "tier": "standard"}
    s.cache["order"] = {"found": True, "order_id": "ORD-1001", "amount": 129.99}
    s.cache["product"] = {"found": True, "product_id": "P001"}
    s.cache["eligibility"] = {
        "eligible": True,
        "reason": "ok",
        "max_refund": 129.99,
        "requires_escalation": False,
    }

    evidence = compute_evidence_confidence(s, 0.55)
    action = compute_action_confidence(s, evidence)

    assert evidence >= CONFIG.escalation_threshold
    assert action >= CONFIG.refund_confidence_floor


@pytest.mark.asyncio
async def test_low_classifier_confidence_does_not_force_refund_escalation():
    from app.agent import process_ticket
    from app.llm import _CLASSIFY_CACHE

    original_mode = CONFIG.mode
    original_chaos = CONFIG.chaos_rate
    _CLASSIFY_CACHE.clear()
    t = Ticket(
        ticket_id="TKT-LOWCONF",
        customer_email="alice.turner@email.com",
        subject="Refund request for headphones",
        body="I bought headphones but they stopped working. Order ORD-1001. I'd like a refund.",
        source="email",
        created_at="2024-03-15T09:12:00Z",
    )
    low_conf_cls = Classification(
        category="refund_request",
        urgency="medium",
        resolvable=True,
        confidence=0.55,
        rationale="low confidence intent",
    )
    reply = Reply(message="Refund processed.", tone="empathetic")
    try:
        object.__setattr__(CONFIG, "mode", "llm")
        object.__setattr__(CONFIG, "chaos_rate", 0.0)
        with patch("app.llm.call_llm_structured", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [low_conf_cls, reply]
            entry = await process_ticket(t, run_id="test_run")
        assert entry.outcome == "resolved"
        assert entry.classifier_confidence == pytest.approx(0.55)
        assert (entry.evidence_confidence or 0) >= CONFIG.escalation_threshold
        assert (entry.action_confidence or 0) >= CONFIG.refund_confidence_floor
    finally:
        _CLASSIFY_CACHE.clear()
        object.__setattr__(CONFIG, "mode", original_mode)
        object.__setattr__(CONFIG, "chaos_rate", original_chaos)


@pytest.mark.asyncio
async def test_end_to_end_single_ticket_rules_mode():
    """Smoke test: one refund ticket through the full loop in rules mode."""
    from app.agent import process_ticket
    from app.models import Ticket

    t = Ticket(
        ticket_id="TKT-001",
        customer_email="alice.turner@email.com",
        subject="Refund request for headphones",
        body="I bought headphones but they stopped working. Order ORD-1001. I'd like a refund.",
        source="email",
        created_at="2024-03-15T09:12:00Z",
    )
    entry = await process_ticket(t, run_id="test_run")
    assert entry.ticket_id == "TKT-001"
    assert entry.outcome in ("resolved", "escalated")
    assert len(entry.tools_used) >= 2
    assert entry.decision_basis in (
        "successful_resolution",
        "recovered_and_resolved",
        "policy_guard",
        "low_confidence",
        "tool_failure",
        "unresolvable_ticket",
        "fraud_detected",
    )


# ---------------------------------------------------------------------------
# Fix 2 — Idempotency for write tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_idempotent_on_second_call():
    """Calling send_reply twice for the same ticket_id must not double-send."""
    ticket_id = "TKT-IDEM-REPLY"
    ikey = _idempotency_key(ticket_id, "send_reply")
    _IDEMPOTENCY.pop(ikey, None)  # ensure clean state

    ctx = {"ticket_id": ticket_id, "attempt": 0}
    first = await send_reply(ctx, ticket_id=ticket_id, message="Hello customer")
    assert first["sent"] is True
    assert "already_sent" not in first

    # Second call — must return cached result with already_sent flag
    second = await send_reply(ctx, ticket_id=ticket_id, message="Hello customer again")
    assert second["sent"] is True
    assert second.get("already_sent") is True

    _IDEMPOTENCY.pop(ikey, None)  # cleanup


@pytest.mark.asyncio
async def test_cancel_order_idempotent_on_second_call():
    """Calling cancel_order twice for the same order_id must return cached result."""
    order_id = "ORD-1012"  # status=processing in fixture
    ikey = _idempotency_key(order_id, "cancel_order")
    _IDEMPOTENCY.pop(ikey, None)

    ctx = {"ticket_id": "TKT-IDEM-CANCEL", "attempt": 0}
    first = await cancel_order(ctx, order_id=order_id)
    assert first.get("cancelled") is True, f"Expected first cancel to succeed, got: {first}"

    second = await cancel_order(ctx, order_id=order_id)
    assert second.get("cancelled") is True
    assert second.get("already_cancelled") is True

    _IDEMPOTENCY.pop(ikey, None)  # cleanup


@pytest.mark.asyncio
async def test_initiate_exchange_idempotent_on_second_call():
    """Calling initiate_exchange twice must return the cached first result."""
    order_id = "ORD-1007"  # exists in fixture
    ikey = _idempotency_key(order_id, "initiate_exchange")
    _IDEMPOTENCY.pop(ikey, None)

    ctx = {"ticket_id": "TKT-IDEM-XCHG", "attempt": 0}
    first = await initiate_exchange(ctx, order_id=order_id, variant="blue")
    assert first.get("initiated") is True, f"Expected first exchange to succeed, got: {first}"

    second = await initiate_exchange(ctx, order_id=order_id, variant="red")
    assert second.get("initiated") is True
    assert second.get("already_initiated") is True
    # The cached variant from the first call must be preserved
    assert second.get("variant") == "blue"

    _IDEMPOTENCY.pop(ikey, None)  # cleanup
