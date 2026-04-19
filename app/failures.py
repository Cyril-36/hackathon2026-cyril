"""Realistic failure injection for tool calls.

Four failure modes, distributed by tool type:
    - timeout         -> asyncio.TimeoutError-ish (we raise TimeoutError)
    - malformed_json  -> returns a broken-JSON string (caught by Pydantic)
    - partial_fields  -> drops a required key (caught by Pydantic)
    - stale_data      -> inconsistent state (e.g. order "pending" but delivered)

Chaos level is global via CONFIG.chaos_rate but can be overridden at runtime
for tests. A deterministic Random seeded from (ticket_id, tool) lets the same
ticket reproduce the same failures for debugging.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any

from app.config import CONFIG


class InjectedFailure(Exception):
    """Base class for injected tool failures. Distinct from real bugs."""


class InjectedTimeout(InjectedFailure):
    pass


class InjectedMalformed(InjectedFailure):
    def __init__(self, payload: str):
        super().__init__("malformed JSON payload")
        self.payload = payload


class InjectedPartial(InjectedFailure):
    def __init__(self, partial: dict[str, Any]):
        super().__init__("partial fields")
        self.partial = partial


class InjectedStale(InjectedFailure):
    def __init__(self, stale: dict[str, Any]):
        super().__init__("stale data")
        self.stale = stale


_FAILURE_MENU: dict[str, list[str]] = {
    "get_order": ["timeout", "stale"],
    "get_customer": ["timeout", "partial"],
    "get_product": ["malformed"],
    "search_knowledge_base": ["timeout", "empty"],
    "check_refund_eligibility": ["throw"],
    "issue_refund": [],          # irreversible: don't inject chaos here
    "send_reply": ["timeout"],
    "escalate": [],              # terminal sink
    "cancel_order": ["timeout"],
    "initiate_exchange": ["timeout"],
}


def _rng(ticket_id: str, tool: str) -> random.Random:
    """Stable seed across processes. Python's str.__hash__ is salted per
    interpreter (PYTHONHASHSEED), so using it here would make --seed
    reruns diverge. SHA-256 is overkill but cheap and deterministic.
    """
    key = f"{CONFIG.seed}|{ticket_id}|{tool}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return random.Random(int.from_bytes(digest[:8], "big", signed=False))


def should_fail(ticket_id: str, tool: str, attempt: int, chaos: float | None = None) -> str | None:
    """Returns a failure tag on injection, else None.

    Failures only fire on attempt==0 so retries have a real chance to succeed
    — exactly the recovery behaviour we want to demonstrate.
    """
    menu = _FAILURE_MENU.get(tool, [])
    if not menu or attempt > 0:
        return None
    rate = CONFIG.chaos_rate if chaos is None else chaos
    rng = _rng(ticket_id, tool)
    # advance rng a bit so same ticket doesn't always fail the same tool first
    _ = rng.random()
    if rng.random() > rate:
        return None
    return rng.choice(menu)


async def apply_failure(tag: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Mutates payload to simulate the failure; may raise."""
    if tag == "timeout":
        await asyncio.sleep(CONFIG.tool_timeout_seconds + 1.0)
        raise InjectedTimeout("tool timed out")
    if tag == "malformed":
        raise InjectedMalformed('{"order_id": "ORD-1001", "amount": 12')
    if tag == "partial":
        # Drop a required field
        partial = {k: v for k, v in payload.items() if k not in {"tier", "email"}}
        raise InjectedPartial(partial)
    if tag == "stale":
        stale = dict(payload)
        # Contradict status vs delivery_date — the classic stale-cache shape
        stale["status"] = "pending"
        stale["delivery_date"] = payload.get("delivery_date")
        raise InjectedStale(stale)
    if tag == "throw":
        raise RuntimeError("eligibility service unavailable")
    if tag == "empty":
        return {"matched": False, "snippets": []}
    return payload
