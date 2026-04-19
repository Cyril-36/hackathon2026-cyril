"""Dataset regression tests — lock in per-ticket behaviour for the 6 tickets
that a reviewer called out as incorrect before the rewrite.

Every test loads the real ticket from data/tickets.json and drives the full
process_ticket loop in rules mode. No mocking — the whole stack runs end-to-end.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("MODE", "rules")
os.environ.setdefault("CHAOS", "0.0")
os.environ.setdefault("SEED", "7")

from app.agent import process_ticket  # noqa: E402
from app.config import CONFIG  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.tools import _IDEMPOTENCY  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


def _load_ticket(ticket_id: str) -> Ticket:
    raw = json.loads((ROOT / "data" / "tickets.json").read_text())
    for item in raw:
        if item["ticket_id"] == ticket_id:
            return Ticket.model_validate(item)
    raise KeyError(ticket_id)


@pytest.fixture(autouse=True)
def _reset_refund_idempotency():
    original_mode = CONFIG.mode
    original_chaos = CONFIG.chaos_rate
    _IDEMPOTENCY.clear()
    object.__setattr__(CONFIG, "mode", "rules")
    object.__setattr__(CONFIG, "chaos_rate", 0.0)
    yield
    _IDEMPOTENCY.clear()
    object.__setattr__(CONFIG, "mode", original_mode)
    object.__setattr__(CONFIG, "chaos_rate", original_chaos)


@pytest.mark.asyncio
async def test_tkt005_vip_extended_return_approved():
    """VIP customer, window expired, pre-approved exception on notes → resolve."""
    entry = await process_ticket(_load_ticket("TKT-005"), run_id="t")
    assert entry.outcome == "resolved"
    assert entry.category == "return_request"
    assert entry.decision_basis == "successful_resolution"


@pytest.mark.asyncio
async def test_tkt006_cancellation_conflict_detected():
    """Customer says 'just placed' but ORD-1006 is already delivered → escalate
    with a conflict reason rather than blindly cancelling or approving."""
    entry = await process_ticket(_load_ticket("TKT-006"), run_id="t")
    assert entry.outcome == "escalated"
    notes = " ".join(s.note for s in entry.reasoning_trace)
    assert "resolved order by email" in notes
    assert "conflict" in notes.lower()


@pytest.mark.asyncio
async def test_tkt008_damaged_on_arrival_refund_checks_eligibility_first():
    """DOA is still a refund flow. KB §1.5 changes the eligibility reason, not the
    irreversible-action guardrail order."""
    entry = await process_ticket(_load_ticket("TKT-008"), run_id="t")
    assert entry.outcome == "resolved"
    assert entry.category == "damaged_on_arrival"
    assert "issue_refund" in entry.tools_used
    assert "check_refund_eligibility" in entry.tools_used
    assert entry.tools_used.index("check_refund_eligibility") < entry.tools_used.index("issue_refund")


@pytest.mark.asyncio
async def test_tkt010_shipping_inquiry_includes_tracking_number():
    """Shipping-inquiry reply must surface TRK-88291 verbatim."""
    entry = await process_ticket(_load_ticket("TKT-010"), run_id="t")
    assert entry.outcome == "resolved"
    assert entry.category == "shipping_inquiry"
    assert "TRK-88291" in (entry.reply_sent or "")


@pytest.mark.asyncio
async def test_tkt013_registered_online_declined_with_both_reasons():
    """Return window expired AND device registered online — decline with both
    reasons, as a first-class outcome rather than a generic escalation."""
    entry = await process_ticket(_load_ticket("TKT-013"), run_id="t")
    assert entry.outcome == "declined"
    assert entry.decision_basis == "policy_guard"
    reply = (entry.reply_sent or "").lower()
    assert "registered online" in reply
    assert "window" in reply or "deadline" in reply


@pytest.mark.asyncio
async def test_tkt018_social_engineering_fraud_detected():
    """Standard customer claims premium policy → fraud_detected, escalated."""
    entry = await process_ticket(_load_ticket("TKT-018"), run_id="t")
    assert entry.outcome == "escalated"
    assert entry.decision_basis == "fraud_detected"


@pytest.mark.asyncio
async def test_tkt020_ambiguous_resolves_with_clarifying_questions():
    """Ambiguous tickets must resolve by asking targeted questions, not escalate
    on low classifier confidence alone."""
    entry = await process_ticket(_load_ticket("TKT-020"), run_id="t")
    assert entry.outcome == "resolved"
    assert entry.category == "ambiguous"
    reply = entry.reply_sent or ""
    assert "order ID" in reply or "order id" in reply.lower()


@pytest.mark.asyncio
async def test_tkt017_invalid_order_asks_for_correct_id():
    """Invalid order ID — reply must explicitly ask for the correct ID,
    not a generic 'a specialist will follow up' boilerplate."""
    entry = await process_ticket(_load_ticket("TKT-017"), run_id="t")
    assert entry.outcome == "escalated"
    reply = (entry.reply_sent or "").lower()
    assert "order id" in reply or "order confirmation" in reply
    # the specific bad ID should appear so the customer knows what we rejected
    bad_id = _load_ticket("TKT-017").body
    import re
    m = re.search(r"\bORD-\d{4}\b", bad_id, re.IGNORECASE)
    if m:
        assert m.group(0) in (entry.reply_sent or "")


@pytest.mark.asyncio
async def test_effective_today_is_ticket_scoped():
    """TKT-002 was created 2024-03-22 (past its 2024-03-19 deadline) but
    CONFIG.today defaults to 2024-03-15. The eligibility check must use the
    ticket's effective_today, not the global — so the refund path must NOT
    complete as a successful issue_refund."""
    entry = await process_ticket(_load_ticket("TKT-002"), run_id="t")
    assert "issue_refund" not in entry.tools_used


@pytest.mark.asyncio
async def test_tkt002_expired_remorse_return_gets_policy_answer_not_warranty():
    """A plain change-of-mind return outside the window should be answered with
    policy guidance, not rerouted as a warranty claim."""
    entry = await process_ticket(_load_ticket("TKT-002"), run_id="t")
    assert entry.category == "return_request"
    assert entry.outcome == "resolved"
    reply = (entry.reply_sent or "").lower()
    assert "window" in reply
    assert "alternative" in reply


@pytest.mark.asyncio
async def test_tkt014_tentative_return_explains_process_without_starting_return():
    """Informational return questions should explain the process and avoid
    implying a return has already been started."""
    entry = await process_ticket(_load_ticket("TKT-014"), run_id="t")
    assert entry.outcome == "resolved"
    reply = (entry.reply_sent or "").lower()
    assert "process" in reply
    assert "no return has been started" in reply


@pytest.mark.asyncio
async def test_tkt016_missing_identity_resolves_with_targeted_questions():
    """No order ID and no known customer record should trigger a clarification
    reply, not a generic escalation."""
    entry = await process_ticket(_load_ticket("TKT-016"), run_id="t")
    assert entry.category == "refund_request"
    assert entry.outcome == "resolved"
    reply = (entry.reply_sent or "").lower()
    assert "order id" in reply
    assert "email" in reply


@pytest.mark.asyncio
async def test_tkt019_policy_answer_mentions_returns_and_exchanges():
    """Policy questions should answer the actual question instead of generic
    'shared policy details' boilerplate."""
    entry = await process_ticket(_load_ticket("TKT-019"), run_id="t")
    assert entry.outcome == "resolved"
    reply = (entry.reply_sent or "").lower()
    assert "return" in reply
    assert "exchange" in reply


@pytest.mark.asyncio
async def test_any_refund_path_checks_eligibility_first():
    """Compliance regression: no ticket may issue a refund before eligibility."""
    raw = json.loads((ROOT / "data" / "tickets.json").read_text())
    for item in raw:
        entry = await process_ticket(Ticket.model_validate(item), run_id="t")
        if "issue_refund" not in entry.tools_used:
            continue
        assert "check_refund_eligibility" in entry.tools_used, entry.ticket_id
        assert entry.tools_used.index("check_refund_eligibility") < entry.tools_used.index("issue_refund"), entry.ticket_id
