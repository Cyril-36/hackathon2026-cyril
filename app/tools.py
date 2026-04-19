"""The 11 tools the agent can call (brief's 8 core + 3 extensions).

All tools are async, all I/O goes through Pydantic at the registry layer.
These are the raw implementations; the registry wraps them with retry,
timeout, validation, chaos injection, and audit bookkeeping.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import CONFIG
from app.failures import apply_failure, should_fail

# ---- Fixture loading (cached) ---------------------------------------------

_CUSTOMERS: dict[str, dict[str, Any]] | None = None
_ORDERS: dict[str, dict[str, Any]] | None = None
_PRODUCTS: dict[str, dict[str, Any]] | None = None
_KB: str | None = None
_IDEMPOTENCY: dict[str, dict[str, Any]] = {}  # key -> result record
_ORDER_LOCKS: dict[str, asyncio.Lock] = {}


def _idempotency_key(entity_id: str, tool: str) -> str:
    """Composite key for the idempotency store: '<tool>:<entity_id>'."""
    return f"{tool}:{entity_id}"


def _load_json(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _customers() -> dict[str, dict[str, Any]]:
    global _CUSTOMERS
    if _CUSTOMERS is None:
        _CUSTOMERS = {c["email"]: c for c in _load_json(CONFIG.customers_path)}
    return _CUSTOMERS


def _orders() -> dict[str, dict[str, Any]]:
    global _ORDERS
    if _ORDERS is None:
        _ORDERS = {o["order_id"]: o for o in _load_json(CONFIG.orders_path)}
    return _ORDERS


def _products() -> dict[str, dict[str, Any]]:
    global _PRODUCTS
    if _PRODUCTS is None:
        _PRODUCTS = {p["product_id"]: p for p in _load_json(CONFIG.products_path)}
    return _PRODUCTS


def _kb_text() -> str:
    global _KB
    if _KB is None:
        _KB = Path(CONFIG.knowledge_base_path).read_text(encoding="utf-8")
    return _KB


def _lock_for(order_id: str) -> asyncio.Lock:
    lock = _ORDER_LOCKS.get(order_id)
    if lock is None:
        lock = asyncio.Lock()
        _ORDER_LOCKS[order_id] = lock
    return lock


def _lock_has_waiters(lock: asyncio.Lock) -> bool:
    """Best-effort cleanup guard for per-order locks.

    asyncio.Lock does not expose waiter count publicly. We only drop the cached
    lock when there are no queued acquirers, which avoids racing a brand-new
    lock against an existing waiter on the old one.
    """
    return bool(getattr(lock, "_waiters", None))


# ---- Tool implementations -------------------------------------------------

# Signature contract for every tool:
#   async def tool(ctx: dict, **kwargs) -> dict
# ctx = {"ticket_id": str, "attempt": int}


async def get_order(ctx: dict, order_id: str) -> dict[str, Any]:
    tag = should_fail(ctx["ticket_id"], "get_order", ctx.get("attempt", 0))
    order = _orders().get(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}
    payload = {"found": True, **order}
    if tag:
        return await apply_failure(tag, payload)
    return payload


async def get_customer(ctx: dict, email: str) -> dict[str, Any]:
    tag = should_fail(ctx["ticket_id"], "get_customer", ctx.get("attempt", 0))
    cust = _customers().get(email)
    if cust is None:
        return {"found": False, "email": email}
    payload = {"found": True, **cust}
    if tag:
        return await apply_failure(tag, payload)
    return payload


async def get_customer_orders(ctx: dict, email: str) -> dict[str, Any]:
    """Look up orders by customer email (used when no order_id supplied)."""
    cust = _customers().get(email)
    if cust is None:
        return {"found": False, "orders": []}
    matches = [o for o in _orders().values() if o["customer_id"] == cust["customer_id"]]
    return {"found": True, "customer_id": cust["customer_id"], "orders": matches}


async def get_product(ctx: dict, product_id: str) -> dict[str, Any]:
    tag = should_fail(ctx["ticket_id"], "get_product", ctx.get("attempt", 0))
    prod = _products().get(product_id)
    if prod is None:
        return {"found": False, "product_id": product_id}
    payload = {"found": True, **prod}
    if tag:
        return await apply_failure(tag, payload)
    return payload


async def search_knowledge_base(ctx: dict, query: str) -> dict[str, Any]:
    tag = should_fail(ctx["ticket_id"], "search_knowledge_base", ctx.get("attempt", 0))
    q_terms = [t for t in query.lower().split() if len(t) > 2]
    lines = _kb_text().splitlines()
    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        hits = sum(1 for term in q_terms if term in low)
        if hits == 0:
            continue
        score = hits * 10
        if stripped.startswith("#"):
            score += 4
        if any(key in low for key in ("must", "never", "eligible", "escalate", "refund")):
            score += 2
        window = [stripped]
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if nxt and not nxt.startswith("#"):
                window.append(nxt)
        scored.append((score, idx, " ".join(window)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    snippets: list[str] = []
    for _, _, snippet in scored:
        if snippet in seen:
            continue
        seen.add(snippet)
        snippets.append(snippet)
        if len(snippets) == 6:
            break
    payload = {"matched": bool(snippets), "snippets": snippets}
    if tag:
        return await apply_failure(tag, payload)
    return payload


async def check_refund_eligibility(
    ctx: dict, order_id: str, today: str | None = None, category: str | None = None
) -> dict[str, Any]:
    tag = should_fail(ctx["ticket_id"], "check_refund_eligibility", ctx.get("attempt", 0))
    if tag:
        # This tool's realistic failure is: it throws.
        return await apply_failure(tag, {})
    order = _orders().get(order_id)
    if order is None:
        return {
            "eligible": False,
            "reason": f"order {order_id} not found",
            "max_refund": 0.0,
            "requires_escalation": False,
        }
    if order.get("refund_status") == "refunded":
        return {
            "eligible": False,
            "reason": "already refunded",
            "max_refund": 0.0,
            "requires_escalation": False,
        }
    today = today or CONFIG.today
    amount = float(order.get("amount", 0.0))
    requires_escalation = amount > CONFIG.refund_escalation_amount
    if category == "damaged_on_arrival":
        return {
            "eligible": True,
            "reason": "damaged on arrival — eligible regardless of return window",
            "max_refund": amount,
            "requires_escalation": requires_escalation,
        }
    deadline = order.get("return_deadline")
    within_window = deadline is None or today <= deadline
    # eligibility decision is conservative; POLICIES layer applies tier leniency.
    if not within_window:
        return {
            "eligible": False,
            "reason": f"return window expired on {deadline}",
            "max_refund": 0.0,
            "requires_escalation": False,
        }
    return {
        "eligible": True,
        "reason": "within return window",
        "max_refund": amount,
        "requires_escalation": requires_escalation,
    }


async def issue_refund(ctx: dict, order_id: str, amount: float) -> dict[str, Any]:
    """Irreversible. Idempotent per order_id via in-memory lock."""
    lock = _lock_for(order_id)
    await lock.acquire()
    try:
        existing = _IDEMPOTENCY.get(order_id)
        if existing is not None:
            return {"issued": False, "reason": "already refunded (idempotent)", **existing}
        order = _orders().get(order_id)
        if order is None:
            return {"issued": False, "reason": "order not found"}
        if order.get("refund_status") == "refunded":
            return {"issued": False, "reason": "already refunded per order record"}
        record = {
            "order_id": order_id,
            "amount": float(amount),
            "status": "refunded",
            "processed_at": CONFIG.today,
        }
        _IDEMPOTENCY[order_id] = record
        return {"issued": True, **record}
    finally:
        lock.release()
        current = _ORDER_LOCKS.get(order_id)
        if current is lock and not lock.locked() and not _lock_has_waiters(lock):
            _ORDER_LOCKS.pop(order_id, None)


async def send_reply(ctx: dict, ticket_id: str, message: str) -> dict[str, Any]:
    # Idempotency: a timed-out send_reply that actually succeeded must not
    # deliver a second email on retry.
    ikey = _idempotency_key(ticket_id, "send_reply")
    existing = _IDEMPOTENCY.get(ikey)
    if existing is not None:
        return {**existing, "already_sent": True}
    tag = should_fail(ctx["ticket_id"], "send_reply", ctx.get("attempt", 0))
    if tag:
        return await apply_failure(tag, {"sent": True, "ticket_id": ticket_id})
    result = {"sent": True, "ticket_id": ticket_id, "chars": len(message)}
    _IDEMPOTENCY[ikey] = result
    return result


async def escalate(ctx: dict, ticket_id: str, summary: str, priority: str) -> dict[str, Any]:
    return {
        "escalated": True,
        "ticket_id": ticket_id,
        "priority": priority,
        "summary": summary[:280],
    }


async def cancel_order(ctx: dict, order_id: str) -> dict[str, Any]:
    # Idempotency: a retried cancel must not double-cancel or return a
    # confusing error if the first attempt already succeeded.
    ikey = _idempotency_key(order_id, "cancel_order")
    existing = _IDEMPOTENCY.get(ikey)
    if existing is not None:
        return {**existing, "already_cancelled": True}
    tag = should_fail(ctx["ticket_id"], "cancel_order", ctx.get("attempt", 0))
    order = _orders().get(order_id)
    if order is None:
        return {"cancelled": False, "reason": "order not found"}
    if order.get("status") != "processing":
        return {
            "cancelled": False,
            "reason": f"cannot cancel: order is {order.get('status')}",
        }
    payload = {"cancelled": True, "order_id": order_id}
    if tag:
        return await apply_failure(tag, payload)
    _IDEMPOTENCY[ikey] = payload
    return payload


async def initiate_exchange(ctx: dict, order_id: str, variant: str) -> dict[str, Any]:
    # Idempotency: retried exchange must not create a duplicate exchange request.
    ikey = _idempotency_key(order_id, "initiate_exchange")
    existing = _IDEMPOTENCY.get(ikey)
    if existing is not None:
        return {**existing, "already_initiated": True}
    tag = should_fail(ctx["ticket_id"], "initiate_exchange", ctx.get("attempt", 0))
    order = _orders().get(order_id)
    if order is None:
        return {"initiated": False, "reason": "order not found"}
    payload = {"initiated": True, "order_id": order_id, "variant": variant}
    if tag:
        return await apply_failure(tag, payload)
    _IDEMPOTENCY[ikey] = payload
    return payload


TOOL_REGISTRY = {
    "get_order": get_order,
    "get_customer": get_customer,
    "get_customer_orders": get_customer_orders,
    "get_product": get_product,
    "search_knowledge_base": search_knowledge_base,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "send_reply": send_reply,
    "escalate": escalate,
    "cancel_order": cancel_order,
    "initiate_exchange": initiate_exchange,
}
