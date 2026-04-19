"""Pure decision functions. No I/O, fully unit-testable.

Encodes the knowledge-base rules: return windows, tier leniency, warranty
routing, social-engineering detection, irreversibility guards.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import CONFIG
from app.models import Category, DecisionBasis
from app.state import TicketState


# Accept the fixture format (`ORD-1001`) plus common customer variants such as
# `ord1001` and `ORD 1001`, then normalize back to the canonical store key.
ORDER_RE = re.compile(r"\bORD(?:[-\s]?)(\d{3,})\b", re.IGNORECASE)
DEFECT_SIGNAL_RE = re.compile(
    r"\b("
    r"stopped working|not working|doesn't work|doesnt work|broken|cracked|"
    r"damaged|defect|defective|manufacturing defect|won't|wont|"
    r"stopped heating|isn't stable|isnt stable|wobbles|arrived with"
    r")\b",
    re.IGNORECASE,
)
TENTATIVE_RETURN_RE = re.compile(
    r"\b("
    r"not sure|might want to return|thinking about returning|thinking about return|"
    r"what'?s the process|what is the process|is it too late|might want|"
    r"considering a return"
    r")\b",
    re.IGNORECASE,
)


def extract_order_id(text: str) -> str | None:
    m = ORDER_RE.search(text or "")
    return f"ORD-{m.group(1)}" if m else None


def has_defect_signal(text: str) -> bool:
    return bool(DEFECT_SIGNAL_RE.search(text or ""))


def is_tentative_return(text: str) -> bool:
    return bool(TENTATIVE_RETURN_RE.search(text or ""))


def effective_today(state: TicketState) -> str:
    """Use the ticket's creation date as 'now' for deterministic policy math.

    Many tickets in the dataset intentionally test stale-window scenarios where
    created_at is a week past the deadline. A global TODAY can't model that —
    each ticket must be evaluated against its own arrival date.
    """
    created = getattr(state.ticket, "created_at", None)
    if isinstance(created, str) and len(created) >= 10:
        return created[:10]
    return CONFIG.today


# ---- Tool-chain templates per category ------------------------------------


CATEGORY_CHAIN: dict[str, list[str]] = {
    "refund_request": [
        "get_customer", "get_order", "get_product",
        "check_refund_eligibility", "issue_refund", "send_reply",
    ],
    "return_request": [
        "get_customer", "get_order", "get_product",
        "check_refund_eligibility", "send_reply",
    ],
    "damaged_on_arrival": [
        "get_customer", "get_order", "get_product",
        "check_refund_eligibility", "issue_refund", "send_reply",
    ],
    "wrong_item": [
        "get_customer", "get_order", "get_product",
        "initiate_exchange", "send_reply",
    ],
    "cancellation": [
        "get_customer", "get_order", "cancel_order", "send_reply",
    ],
    "shipping_inquiry": [
        "get_customer", "get_order", "send_reply",
    ],
    "refund_status_check": [
        "get_customer", "get_order", "send_reply",
    ],
    "warranty_claim": [
        "get_customer", "get_order", "get_product", "escalate",
    ],
    "policy_question": [
        "search_knowledge_base", "send_reply",
    ],
    "social_engineering": [
        "get_customer", "escalate",
    ],
    "ambiguous": [
        "search_knowledge_base", "send_reply",
    ],
}


def chain_template(category: Category) -> list[str]:
    return list(CATEGORY_CHAIN.get(category, CATEGORY_CHAIN["ambiguous"]))


# ---- Guards ---------------------------------------------------------------


def refund_guard(state: TicketState) -> tuple[bool, str]:
    """Block issue_refund unless it's authorised under one of the KB rules.

    Paths to approval:
      - damaged_on_arrival  → KB §1.5: refund regardless of return window,
        but still only after eligibility is checked.
      - eligibility positive → standard path.
      - eligibility negative + VIP pre-approved extension on customer notes.
    """
    if "check_refund_eligibility" not in state.tools_used:
        return False, "eligibility not checked"
    elig = state.cache.get("eligibility")
    if elig is None:
        return False, "eligibility result missing"
    if not elig.get("eligible"):
        cust = state.cache.get("customer") or {}
        if _has_preapproved_exception(cust):
            return True, "eligibility negative but VIP pre-approval on file"
        return False, f"eligibility negative: {elig.get('reason','?')}"
    # High-value refunds (> refund_escalation_amount) require a human — even
    # when eligibility is positive. VIP override already moved these to
    # vip_override=True earlier, so this only bites the standard-tier path.
    if elig.get("requires_escalation") and not elig.get("vip_override"):
        amount = elig.get("max_refund", 0.0)
        return (
            False,
            f"refund amount ${amount:.2f} exceeds auto-approval threshold "
            f"${CONFIG.refund_escalation_amount:.0f} — human approval required",
        )
    return True, "eligibility confirmed"


def _has_preapproved_exception(customer: dict[str, Any]) -> bool:
    notes = (customer.get("notes") or "").lower()
    if customer.get("tier") != "vip":
        return False
    return any(
        kw in notes
        for kw in ["pre-approved", "pre approved", "extended return", "management pre-approval"]
    )


def has_vip_extension(customer: dict[str, Any] | None) -> bool:
    """Public wrapper for the VIP pre-approved-extension check."""
    return _has_preapproved_exception(customer or {})


# ---- Dataset-aware helpers --------------------------------------------------


_TRACKING_RE = re.compile(r"\bTRK-\d+\b")
_ORDER_IN_NOTES_RE = re.compile(r"\bregistered online\b", re.IGNORECASE)
_CLAIM_NEW_ORDER = (
    "just placed",
    "placed yesterday",
    "placed just now",
    "new order",
    "before it ships",
    "i just ordered",
)


def extract_tracking_number(order: dict[str, Any] | None) -> str | None:
    if not order:
        return None
    notes = order.get("notes") or ""
    m = _TRACKING_RE.search(notes)
    return m.group(0) if m else None


def detect_order_conflict(ticket_body: str, order: dict[str, Any] | None) -> str | None:
    """Flag contradiction: customer says 'just placed' but order is already delivered.

    Returns a short reason string when a conflict is detected, else None.
    """
    if not order or not order.get("found"):
        return None
    body = (ticket_body or "").lower()
    status = (order.get("status") or "").lower()
    claims_recent = any(kw in body for kw in _CLAIM_NEW_ORDER)
    if claims_recent and status in {"shipped", "delivered"}:
        return (
            f"customer says they just placed the order, but order {order.get('order_id')} "
            f"is already {status}"
        )
    return None


def is_registered_online(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    return bool(_ORDER_IN_NOTES_RE.search(order.get("notes") or ""))


def detect_social_engineering(ticket_body: str, customer: dict[str, Any] | None) -> tuple[bool, str]:
    """Claim of tier/privilege not supported by system record."""
    body = ticket_body.lower()
    claims_premium = "premium member" in body or "premium policy" in body
    claims_instant = "instant refund" in body
    claims_vip = "vip member" in body or "vip policy" in body
    if not (claims_premium or claims_instant or claims_vip):
        return False, ""
    if customer is None:
        return True, "customer not in system but claims tier privileges"
    actual = customer.get("tier")
    if claims_premium and actual != "premium":
        return True, f"claims premium but system tier is {actual}"
    if claims_vip and actual != "vip":
        return True, f"claims vip but system tier is {actual}"
    if claims_instant:
        return True, "claims an 'instant refund' policy that does not exist"
    return False, ""


# ---- Confidence & decision basis ------------------------------------------


def _clamp_conf(value: float) -> float:
    return max(0.0, min(1.0, value))


def state_evidence_confidence(state: TicketState) -> float:
    return (
        state.evidence_confidence
        if state.evidence_confidence is not None
        else state.confidence
    )


def state_action_confidence(state: TicketState) -> float:
    if state.action_confidence is not None:
        return state.action_confidence
    if state.evidence_confidence is not None:
        return state.evidence_confidence
    return state.confidence


def compute_evidence_confidence(state: TicketState, classifier_confidence: float) -> float:
    """Blend classifier confidence with concrete system evidence.

    The classifier picks an intent; evidence from tools determines whether the
    agent actually understands the case well enough to proceed. This lets a
    slightly uncertain label recover once order / eligibility data confirms it.
    """
    conf = classifier_confidence

    customer = state.cache.get("customer") or {}
    order = state.cache.get("order") or {}
    product = state.cache.get("product") or {}
    eligibility = state.cache.get("eligibility") or {}
    kb = state.cache.get("kb") or {}

    if customer.get("customer_id"):
        conf += 0.04
    if order.get("found"):
        conf += 0.08
    if product.get("found"):
        conf += 0.03
    if state.cache.get("order_lookup_by_email"):
        conf += 0.03
    if eligibility:
        conf += 0.12
        if eligibility.get("eligible") or eligibility.get("vip_override"):
            conf += 0.06
    if kb.get("matched"):
        conf += 0.06

    if state.cache.get("order_missing"):
        conf -= 0.30
    if state.cache.get("conflict"):
        conf -= 0.25
    if state.cache.get("fraud_flag"):
        conf -= 0.35
    if state.cache.get("guard_blocked"):
        conf -= 0.10

    unrecovered = sum(1 for f in state.failures if not f.recovered)
    recovered = sum(1 for f in state.failures if f.recovered)
    conf -= 0.20 * unrecovered
    conf -= 0.02 * recovered

    return _clamp_conf(conf)


def compute_action_confidence(state: TicketState, evidence_confidence: float) -> float:
    """Estimate whether the chosen action is safe, not just well-classified."""
    conf = evidence_confidence

    if not state.intends_irreversible:
        return _clamp_conf(conf)

    if state.cache.get("order_missing") or state.cache.get("conflict"):
        return 0.0
    if state.any_unrecovered():
        return _clamp_conf(conf - 0.15)

    eligibility = state.cache.get("eligibility") or {}
    if not eligibility:
        return _clamp_conf(min(conf, 0.50))
    if not eligibility.get("eligible") and not eligibility.get("vip_override"):
        return _clamp_conf(min(conf, 0.40))
    if eligibility.get("requires_escalation") and not eligibility.get("vip_override"):
        return _clamp_conf(min(conf, 0.70))

    if state.cache.get("order", {}).get("found"):
        conf = max(conf, 0.90)
    if eligibility.get("vip_override"):
        conf = max(conf, CONFIG.refund_confidence_floor)

    return _clamp_conf(conf)


def adjust_confidence(state: TicketState, base: float) -> float:
    """Legacy wrapper kept for older callers and tests."""
    return compute_evidence_confidence(state, base)


def compute_decision_basis(state: TicketState) -> DecisionBasis:
    evidence_conf = state_evidence_confidence(state)
    action_conf = state_action_confidence(state)

    # Priority 1: hard signals that override everything
    if state.cache.get("fraud_flag"):
        return "fraud_detected"
    if state.any_unrecovered():
        return "tool_failure"

    # Declined outcomes are policy-driven (non-returnable, etc.)
    if state.outcome == "declined":
        return "policy_guard"

    # Priority 2: escalated outcomes need an honest reason
    if state.outcome == "escalated":
        meaningful = any(
            t in state.tools_used
            for t in (
                "get_order",
                "get_customer",
                "check_refund_eligibility",
                "search_knowledge_base",
                "get_customer_orders",
            )
        )
        if not meaningful:
            return "unresolvable_ticket"
        if evidence_conf < CONFIG.escalation_threshold:
            return "low_confidence"
        if state.cache.get("guard_blocked"):
            return "policy_guard"
        if state.category in ("warranty_claim",):
            return "policy_guard"
        if state.intends_irreversible and action_conf < CONFIG.refund_confidence_floor:
            return "policy_guard"
        # Escalated but we had enough info — ticket inherently needs a human
        return "unresolvable_ticket"

    # Priority 3: resolved outcomes
    if state.recovery_attempted:
        return "recovered_and_resolved"
    return "successful_resolution"


def compute_escalation_reason(state: TicketState) -> str:
    """Human-readable summary for the escalation payload.

    Surfaces the most actionable reason first so a human reviewer sees it in
    the audit log without having to read the full reasoning_trace. In
    particular, a missing/unknown order_id is the single most common reason
    a guard fires on a refund_request — we prepend it so the summary is
    honest about *why* the guard tripped, not just that one did.
    """
    bits: list[str] = []
    bits.append(f"category={state.category}")
    bits.append(
        "confidence="
        f"classifier:{(state.classifier_confidence if state.classifier_confidence is not None else state.confidence):.2f}/"
        f"evidence:{state_evidence_confidence(state):.2f}/"
        f"action:{state_action_confidence(state):.2f}"
    )
    # Elevate the concrete root-cause before the generic guard message so the
    # log entry reads like "order ORD-9999 not found" instead of the opaque
    # "guard=eligibility not checked".
    if state.cache.get("order_missing"):
        bits.append(f"order_missing={state.cache['order_missing']}")
    if state.any_unrecovered():
        tools = ", ".join(f.tool for f in state.failures if not f.recovered)
        bits.append(f"unrecovered_failures=[{tools}]")
    if state.cache.get("fraud_flag"):
        bits.append(f"fraud_reason={state.cache['fraud_flag']}")
    if state.cache.get("guard_blocked"):
        bits.append(f"guard={state.cache['guard_blocked']}")
    if state.ticket.expected_action:
        bits.append(f"expected={state.ticket.expected_action[:100]}")
    return " | ".join(bits)


def compute_escalation_brief(state: TicketState) -> str:
    """One-paragraph plain-English summary for the human agent picking this up.

    Unlike escalation_summary (machine-readable `|`-joined bits), this reads
    as a natural sentence so a support manager can act without parsing code.
    """
    parts: list[str] = []
    category_label = state.category.replace("_", " ").title()
    parts.append(f"Category: {category_label}.")

    if state.cache.get("fraud_flag"):
        parts.append(
            f"Fraud signal detected: {state.cache['fraud_flag']}. "
            "Do not process any refund without identity verification."
        )
    elif state.any_unrecovered():
        tools = ", ".join(f.tool for f in state.failures if not f.recovered)
        parts.append(
            f"System tool failures blocked resolution ({tools}). "
            "Retry manually or check service health before re-queuing."
        )
    elif state.cache.get("order_missing"):
        parts.append(
            f"Could not locate order: {state.cache['order_missing']}. "
            "Ask the customer for their order ID (starts with ORD-) and the email used at checkout."
        )
    elif state.cache.get("guard_blocked"):
        parts.append(f"Policy guard triggered: {state.cache['guard_blocked']}.")
    elif state.category == "warranty_claim":
        parts.append(
            "Warranty claims require manual assessment. "
            "Verify the product's warranty period and the customer's description of the defect."
        )
    else:
        esc_conf = state_evidence_confidence(state)
        parts.append(
            f"Agent confidence too low to auto-resolve ({esc_conf:.0%}). "
            "Review the reasoning trace for the full picture before responding."
        )

    tried = ", ".join(state.tools_used) if state.tools_used else "none"
    parts.append(f"Agent tools used: {tried}.")
    return " ".join(parts)


# ---- Priority mapping ------------------------------------------------------


URGENCY_TO_PRIORITY = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "urgent": "urgent",
}


def escalation_priority(state: TicketState) -> str:
    if state.cache.get("fraud_flag"):
        return "urgent"
    if state.category == "warranty_claim":
        return "medium"
    return URGENCY_TO_PRIORITY.get(state.urgency, "medium")


# ---- Return-window + warranty math ----------------------------------------


def within_return_window(order: dict[str, Any], today: str) -> bool:
    deadline = order.get("return_deadline")
    if deadline is None:
        return False
    return today <= deadline


def warranty_active(order: dict[str, Any], product: dict[str, Any], today: str) -> bool:
    delivery = order.get("delivery_date")
    months = int(product.get("warranty_months", 0) or 0)
    if not delivery or months <= 0:
        return False
    # cheap date math; good enough for ISO-8601 dates
    y, m, d = (int(x) for x in delivery.split("-"))
    exp_y = y + (m - 1 + months) // 12
    exp_m = (m - 1 + months) % 12 + 1
    expiry = f"{exp_y:04d}-{exp_m:02d}-{d:02d}"
    return today <= expiry
