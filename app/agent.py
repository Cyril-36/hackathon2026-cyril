"""Agent orchestrator: CLASSIFY -> PLAN -> ACT -> VERIFY -> EVALUATE -> RESOLVE/ESCALATE -> LOG.

Every state mutation is logged to state.reasoning_trace. Every tool call goes
through registry.call_tool. The finished AuditEntry is written by the runner.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app import llm, policies
from app.config import CONFIG
from app.models import AuditEntry, Customer, Order, Product, RefundEligibility, Ticket
from app.registry import RegistryError, call_tool
from app.state import TicketState


async def process_ticket(
    ticket: Ticket,
    run_id: str,
    emitter: Any | None = None,
) -> AuditEntry:
    state = TicketState(ticket=ticket, emitter=emitter)
    if state.emitter is not None:
        state.emitter.emit(
            "ticket_start",
            subject=ticket.subject,
            customer_email=ticket.customer_email,
            tier=ticket.tier,
            source=ticket.source,
        )
    state.log("start", f"ticket {ticket.ticket_id} mode={CONFIG.mode}")

    # 1. CLASSIFY
    cls = await llm.classify_ticket(state)
    state.category = cls.category
    state.urgency = cls.urgency
    state.classifier_confidence = cls.confidence
    if state.emitter is not None:
        state.emitter.emit(
            "classify",
            category=cls.category,
            urgency=cls.urgency,
            confidence=round(cls.confidence, 3),
            classifier_confidence=round(cls.confidence, 3),
        )

    # 2. PLAN — chain template from policies + fraud override
    await _plan(state)

    # 3. ACT — drive the chain, registering failures/recoveries
    await _act(state)

    # 4. VERIFY — safety/correctness checks before writes
    verify_ok = _verify(state)
    state.log("verify_result", f"ok={verify_ok} irreversible={state.intends_irreversible}")

    # 5. EVALUATE — self-awareness (confidence + recovery)
    state.evidence_confidence = policies.compute_evidence_confidence(
        state,
        state.classifier_confidence,
    )
    state.action_confidence = policies.compute_action_confidence(
        state,
        state.evidence_confidence,
    )
    state.confidence = state.action_confidence
    state.log(
        "evaluate",
        "conf="
        f"classifier={state.classifier_confidence:.2f} "
        f"evidence={state.evidence_confidence:.2f} "
        f"action={state.action_confidence:.2f} "
        f"recovered={state.recovery_attempted} "
        f"unrecovered_failures={sum(1 for f in state.failures if not f.recovered)}",
    )
    if state.emitter is not None:
        state.emitter.emit(
            "decide",
            verify_ok=verify_ok,
            confidence=round(state.action_confidence, 3),
            evidence_confidence=round(state.evidence_confidence, 3),
            action_confidence=round(state.action_confidence, 3),
            irreversible=state.intends_irreversible,
        )

    # 6. RESOLVE or ESCALATE
    await _decide_and_write(state, verify_ok)

    # 7. LOG — build audit entry
    basis = policies.compute_decision_basis(state)
    duration_ms = int((time.time() - state.started_at) * 1000)
    if state.emitter is not None:
        state.emitter.emit(
            "ticket_done",
            outcome=state.outcome or "escalated",
            decision_basis=basis,
            confidence=round(state.action_confidence, 3),
            classifier_confidence=round(state.classifier_confidence, 3),
            evidence_confidence=round(state.evidence_confidence, 3),
            action_confidence=round(state.action_confidence, 3),
            duration_ms=duration_ms,
            reply_sent=state.reply_sent,
            escalation_summary=state.escalation_summary,
            recovery_attempted=state.recovery_attempted,
            tools_used=list(state.tools_used),
            category=state.category,
            urgency=state.urgency,
        )
    return AuditEntry(
        run_id=run_id,
        ticket_id=ticket.ticket_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode=CONFIG.mode,
        category=state.category,
        urgency=state.urgency,
        outcome=state.outcome or "escalated",  # type: ignore[arg-type]
        confidence=round(state.action_confidence, 3),
        classifier_confidence=round(state.classifier_confidence, 3),
        evidence_confidence=round(state.evidence_confidence, 3),
        action_confidence=round(state.action_confidence, 3),
        tools_used=list(state.tools_used),
        failures=list(state.failures),
        recovery_attempted=state.recovery_attempted,
        decision_basis=basis,
        duration_ms=duration_ms,
        reasoning_trace=list(state.reasoning_trace),
        expected_action=ticket.expected_action,
        reply_sent=state.reply_sent,
        escalation_summary=state.escalation_summary,
        escalation_brief=state.escalation_brief,
        llm_calls=state.llm_calls,
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
    )


# ---- Step helpers ---------------------------------------------------------


async def _plan(state: TicketState) -> None:
    order_id = policies.extract_order_id(state.ticket.body) or policies.extract_order_id(
        state.ticket.subject
    )
    state.cache["order_id"] = order_id

    # KB §7: customer requesting replacement (not refund) for damaged item -> escalate
    body_lower = state.ticket.body.lower()
    if state.category == "damaged_on_arrival" and (
        "replacement" in body_lower or "not a refund" in body_lower
    ):
        state.log("plan", "damaged item + replacement requested — route to fulfilment team")
        state.category = "warranty_claim"
        state.cache["replacement_requested"] = True

    chain = policies.chain_template(state.category)
    state.cache["chain"] = chain
    state.intends_irreversible = "issue_refund" in chain
    state.log("plan", f"chain={chain} order_id={order_id or 'none'}")


async def _act(state: TicketState) -> None:
    """Run the planned chain, adapting as new information arrives."""
    email = state.ticket.customer_email
    order_id = state.cache.get("order_id")
    ticket_text = f"{state.ticket.subject} {state.ticket.body}"

    # ---- Customer lookup ---------------------------------------------------
    customer: dict[str, Any] | None = None
    if "get_customer" in state.cache["chain"]:
        try:
            cust = await call_tool(
                "get_customer", state, response_schema=Customer, email=email
            )
            if cust.get("found"):
                customer = cust
                state.cache["customer"] = cust
                state.log("act", f"customer {cust.get('customer_id')} tier={cust.get('tier')}")
            else:
                state.log("act", f"customer {email} not in system")
        except RegistryError as e:
            state.log("act", f"get_customer failed: {e.reason}")

    # ---- Fraud check short-circuits ---------------------------------------
    fraud, reason = policies.detect_social_engineering(state.ticket.body, customer)
    if fraud:
        state.cache["fraud_flag"] = reason
        state.log("verify", f"fraud signal: {reason}")
        state.category = "social_engineering"
        state.cache["chain"] = ["escalate"]
        return

    # ---- Email → order lookup when no ID provided -------------------------
    if order_id is None and any(
        t in state.cache["chain"]
        for t in ("get_order", "check_refund_eligibility", "cancel_order", "issue_refund")
    ):
        try:
            lookup = await call_tool("get_customer_orders", state, email=email)
            if lookup.get("found") and lookup.get("orders"):
                orders = sorted(
                    lookup["orders"],
                    key=lambda o: o.get("order_date", ""),
                    reverse=True,
                )
                order_id = orders[0]["order_id"]
                state.cache["order_id"] = order_id
                state.cache["order_lookup_by_email"] = True
                state.log("act", f"resolved order by email -> {order_id}")
            else:
                state.log("act", f"no orders found for {email}")
        except RegistryError as e:
            state.log("act", f"get_customer_orders failed: {e.reason}")

    # ---- get_order --------------------------------------------------------
    order: dict[str, Any] | None = None
    if "get_order" in state.cache["chain"] and order_id:
        try:
            order = await call_tool(
                "get_order", state, response_schema=Order, order_id=order_id
            )
            if order.get("found"):
                state.cache["order"] = order
                state.log(
                    "act",
                    f"order {order_id} status={order.get('status')} refund={order.get('refund_status')}",
                )
                # Conflict detection: customer claim vs system record
                conflict = policies.detect_order_conflict(state.ticket.body, order)
                if conflict:
                    state.cache["conflict"] = conflict
                    state.log("verify", f"conflict: {conflict}")
            else:
                state.cache["order_missing"] = order_id
                state.log("act", f"order {order_id} not found — flag for escalation")
        except RegistryError as e:
            state.log("act", f"get_order failed: {e.reason}")

    # ---- get_product ------------------------------------------------------
    product: dict[str, Any] | None = None
    if "get_product" in state.cache["chain"] and order and order.get("product_id"):
        try:
            product = await call_tool(
                "get_product", state, response_schema=Product, product_id=order["product_id"]
            )
            if product.get("found"):
                state.cache["product"] = product
                state.log(
                    "act",
                    f"product {product['product_id']} warranty={product['warranty_months']}m window={product['return_window_days']}d",
                )
        except RegistryError as e:
            state.log("act", f"get_product failed: {e.reason}")

    # ---- Knowledge base ---------------------------------------------------
    if "search_knowledge_base" in state.cache["chain"]:
        try:
            kb = await call_tool(
                "search_knowledge_base",
                state,
                query=f"{state.ticket.subject} {state.ticket.body[:120]}",
            )
            state.cache["kb"] = kb
            state.log("act", f"kb matched={kb.get('matched')} snippets={len(kb.get('snippets',[]))}")
        except RegistryError as e:
            state.log("act", f"kb search failed: {e.reason}")

    # ---- Policy layer: non-returnable + warranty reroute ------------------
    today = policies.effective_today(state)
    cust_ctx = state.cache.get("customer") or {}
    has_vip_ext = policies.has_vip_extension(cust_ctx)

    if order and policies.is_registered_online(order):
        state.cache["non_returnable"] = "device registered online — non-returnable per KB §1.3"
        state.log("verify", state.cache["non_returnable"])

    if order and product:
        if (
            not policies.within_return_window(order, today)
            and policies.warranty_active(order, product, today)
            and state.category in ("refund_request", "return_request")
            and policies.has_defect_signal(ticket_text)
            and not has_vip_ext
            and not state.cache.get("non_returnable")
        ):
            state.log(
                "verify",
                "return window expired but warranty active — reroute to warranty team",
            )
            state.category = "warranty_claim"
            state.cache["chain"] = ["escalate"]
            state.intends_irreversible = False
            return
        if has_vip_ext and not policies.within_return_window(order, today):
            state.log("verify", "VIP pre-approved extended-return exception applies")
            state.cache["vip_extension_applied"] = True

    # ---- Refund eligibility ----------------------------------------------
    eligibility: dict[str, Any] | None = None
    if (
        "check_refund_eligibility" in state.cache["chain"]
        and order_id
        and order
        and order.get("found")
    ):
        try:
            eligibility = await call_tool(
                "check_refund_eligibility",
                state,
                response_schema=RefundEligibility,
                order_id=order_id,
                today=today,
                category=state.category,
            )
            state.cache["eligibility"] = eligibility
            state.log(
                "verify",
                f"eligibility: {eligibility.get('eligible')} ({eligibility.get('reason','')})",
            )
            # VIP extended-return override: KB §2.3 — management pre-approval on file.
            if (
                not eligibility.get("eligible")
                and has_vip_ext
                and state.category in ("refund_request", "return_request")
                and not state.cache.get("non_returnable")
            ):
                state.cache["eligibility"] = {
                    "eligible": True,
                    "reason": "VIP pre-approved extended-return exception (KB §2.3)",
                    "max_refund": float(order.get("amount", 0.0)),
                    "requires_escalation": False,
                    "vip_override": True,
                }
                state.log(
                    "verify",
                    "VIP pre-approved exception → eligibility overridden to approved",
                )
            if (
                state.category == "refund_request"
                and not state.cache.get("non_returnable")
                and not eligibility.get("eligible")
                and str(eligibility.get("reason", "")).startswith("return window expired")
                and not policies.has_defect_signal(ticket_text)
            ):
                state.category = "return_request"
                state.intends_irreversible = False
                state.cache["chain"] = [
                    tool for tool in state.cache["chain"] if tool != "issue_refund"
                ]
                state.log(
                    "verify",
                    "expired non-defect refund request — answer as return policy guidance",
                )
        except RegistryError as e:
            state.log("act", f"eligibility failed: {e.reason}")

    if (
        state.category in (
            "refund_request",
            "return_request",
            "damaged_on_arrival",
            "wrong_item",
            "cancellation",
            "shipping_inquiry",
            "refund_status_check",
        )
        and not state.cache.get("order")
        and not state.cache.get("order_missing")
        and not state.cache.get("conflict")
        and not state.any_unrecovered()
    ):
        state.cache["clarification_needed"] = "order_identification"
        state.intends_irreversible = False
        state.log("verify", "need order ID / registered email before we can act")


def _verify(state: TicketState) -> bool:
    """Gate writes. Returns True if we're clear to act."""
    if state.cache.get("clarification_needed"):
        state.log("verify", "clarification path permitted")
        return True

    # Non-returnable is a hard block on any refund/return write.
    if state.cache.get("non_returnable") and state.category in (
        "refund_request",
        "return_request",
        "damaged_on_arrival",
    ):
        state.cache["guard_blocked"] = state.cache["non_returnable"]
        state.log("verify", f"blocked: {state.cache['non_returnable']}")
        return False

    # Conflicting data (customer claim vs record) is never safe to auto-act on.
    if state.cache.get("conflict"):
        state.cache["guard_blocked"] = state.cache["conflict"]
        state.log("verify", f"blocked: {state.cache['conflict']}")
        return False

    if state.intends_irreversible:
        ok, reason = policies.refund_guard(state)
        if not ok:
            state.cache["guard_blocked"] = reason
            state.log("verify", f"refund blocked: {reason}")
            return False
        state.log("verify", f"refund guard passed: {reason}")

    if state.category in ("refund_request", "return_request") and "order" not in state.cache:
        state.cache["guard_blocked"] = "order record unavailable"
        state.log("verify", "blocked: order record unavailable")
        return False

    if state.any_unrecovered():
        critical = {
            "get_order",
            "get_customer",
            "check_refund_eligibility",
            "issue_refund",
        }
        if any(not f.recovered and f.tool in critical for f in state.failures):
            state.log("verify", "blocked: critical tool unrecovered")
            return False
    return True


async def _decide_and_write(state: TicketState, verify_ok: bool) -> None:
    """Apply the final write — refund/cancel/exchange/reply OR escalate."""
    # Registered-online decline is a first-class outcome, not an escalation.
    if state.cache.get("non_returnable") and state.category in (
        "refund_request",
        "return_request",
        "damaged_on_arrival",
    ):
        await _decline_non_returnable(state)
        return

    # Irreversible actions require both policy approval and model confidence.
    if (
        state.intends_irreversible
        and policies.state_action_confidence(state) < CONFIG.refund_confidence_floor
        and not state.cache.get("eligibility", {}).get("vip_override")
    ):
        state.log(
            "decide",
            f"action confidence {policies.state_action_confidence(state):.2f} below refund floor "
            f"{CONFIG.refund_confidence_floor} — escalating",
        )
        verify_ok = False
        state.cache["guard_blocked"] = "irreversible-confidence-floor"

    # Ambiguous tickets resolve by asking targeted clarifying questions — that
    # read-only action is exactly the right response to low classifier confidence.
    can_clarify = (
        (
            state.category == "ambiguous"
            or bool(state.cache.get("clarification_needed"))
        )
        and verify_ok
        and not state.any_unrecovered()
    )

    should_escalate = (
        not verify_ok
        or (
            policies.state_evidence_confidence(state) < CONFIG.escalation_threshold
            and not can_clarify
        )
        or state.category in ("social_engineering", "warranty_claim")
        or "order_missing" in state.cache
        or state.any_unrecovered()
    )

    if should_escalate:
        await _escalate(state)
        return

    try:
        if state.category == "refund_request" and state.intends_irreversible:
            await _resolve_refund(state)
            return

        if state.category == "damaged_on_arrival":
            await _resolve_doa(state)
            return

        if state.category == "cancellation":
            await _resolve_cancellation(state)
            return

        if state.category == "wrong_item":
            await _resolve_wrong_item(state)
            return

        # Read-only / advisory paths
        await _resolve_readonly(state)

    except RegistryError as e:
        state.log("resolve_failed", f"{e.tool}: {e.reason}")
        await _escalate(state)


# ---- Category handlers ----------------------------------------------------


async def _resolve_refund(state: TicketState) -> None:
    order = state.cache["order"]
    amount = float(
        state.cache.get("eligibility", {}).get("max_refund")
        or order.get("amount", 0.0)
    )
    res = await call_tool(
        "issue_refund", state, order_id=order["order_id"], amount=amount
    )
    state.log("resolve", f"refund issued: {res.get('issued')} ${amount:.2f}")
    reply = await llm.draft_reply(
        state,
        {
            "outcome": "resolved",
            "action_summary": f"processed a full refund of ${amount:.2f}",
            "facts": {"amount": f"${amount:.2f}", "order_id": order["order_id"]},
        },
    )
    await call_tool(
        "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
    )
    state.reply_sent = reply.message
    state.outcome = "resolved"
    state.log("resolve", "refund_request — reply sent, ticket closed")


async def _resolve_doa(state: TicketState) -> None:
    order = state.cache["order"]
    amount = float(
        state.cache.get("eligibility", {}).get("max_refund")
        or order.get("amount", 0.0)
    )
    await call_tool(
        "issue_refund", state, order_id=order["order_id"], amount=amount
    )
    summary = f"processed a full refund of ${amount:.2f} — no return needed (damaged on arrival)"
    reply = await llm.draft_reply(
        state,
        {
            "outcome": "resolved",
            "action_summary": summary,
            "facts": {"amount": f"${amount:.2f}", "order_id": order["order_id"]},
        },
    )
    await call_tool(
        "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
    )
    state.reply_sent = reply.message
    state.outcome = "resolved"
    state.log("resolve", "damaged_on_arrival — refund issued per KB §1.5")


async def _resolve_cancellation(state: TicketState) -> None:
    order = state.cache.get("order") or {}
    order_id = order.get("order_id")
    if not order_id:
        state.cache["guard_blocked"] = "order record unavailable"
        await _escalate(state)
        return
    cr = await call_tool("cancel_order", state, order_id=order_id)
    state.cache["cancel_result"] = cr
    if cr.get("cancelled"):
        reply = await llm.draft_reply(
            state, {"outcome": "resolved", "action_summary": "cancelled your order"}
        )
        await call_tool(
            "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
        )
        state.reply_sent = reply.message
        state.outcome = "resolved"
        return
    # cancellation denied — order already shipped/delivered
    state.cache["guard_blocked"] = cr.get("reason", "cannot cancel under policy")
    await _escalate(state)


async def _resolve_wrong_item(state: TicketState) -> None:
    order = state.cache.get("order") or {}
    order_id = order.get("order_id")
    if not order_id:
        state.cache["guard_blocked"] = "order record unavailable"
        await _escalate(state)
        return
    res = await call_tool(
        "initiate_exchange", state, order_id=order_id, variant="correct"
    )
    state.cache["exchange_result"] = res
    if not res.get("initiated"):
        state.cache["guard_blocked"] = res.get("reason", "exchange could not be started")
        await _escalate(state)
        return
    reply = await llm.draft_reply(
        state,
        {
            "outcome": "resolved",
            "action_summary": "started an exchange for the correct item",
        },
    )
    await call_tool(
        "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
    )
    state.reply_sent = reply.message
    state.outcome = "resolved"
    state.log("resolve", "wrong_item — reply sent, ticket closed")


async def _resolve_readonly(state: TicketState) -> None:
    summary, facts = _summary_for_readonly(state)
    reply = await llm.draft_reply(
        state,
        {"outcome": "resolved", "action_summary": summary, "facts": facts},
    )
    await call_tool(
        "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
    )
    state.reply_sent = reply.message
    state.outcome = "resolved"


async def _decline_non_returnable(state: TicketState) -> None:
    """Politely decline a return/refund where KB says the item is non-returnable."""
    order = state.cache.get("order") or {}
    product = state.cache.get("product") or {}
    today = policies.effective_today(state)
    deadline = order.get("return_deadline") or "unknown"

    reasons: list[str] = []
    if policies.is_registered_online(order):
        reasons.append(
            "the device was registered online, which makes it non-returnable per our policy (KB §1.3)"
        )
    if order and deadline and today > deadline:
        reasons.append(f"the 30-day return window ended on {deadline}")

    summary = "declined the return — " + "; ".join(reasons)
    facts = {
        "product": product.get("name", ""),
        "return_deadline": deadline,
        "reasons": reasons,
    }
    reply = await llm.draft_reply(
        state,
        {
            "outcome": "declined",
            "action_summary": summary,
            "facts": facts,
        },
    )
    await call_tool(
        "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
    )
    state.reply_sent = reply.message
    state.outcome = "declined"
    state.escalation_summary = summary
    state.log("resolve", f"declined non-returnable: {summary[:140]}")


def _summary_for_readonly(state: TicketState) -> tuple[str, dict[str, Any]]:
    order = state.cache.get("order") or {}
    facts: dict[str, Any] = {}
    ticket_text = f"{state.ticket.subject} {state.ticket.body}"

    if state.cache.get("clarification_needed") == "order_identification":
        return (
            "asked for the order ID and registered email before we can process this request",
            {"questions": [
                "What is the order ID from your confirmation email (it starts with ORD-)?",
                "What email address did you use at checkout?",
            ]},
        )

    if state.category == "shipping_inquiry":
        tracking = policies.extract_tracking_number(order)
        status = order.get("status", "in transit")
        facts = {
            "order_id": order.get("order_id", ""),
            "status": status,
            "tracking_number": tracking or "",
            "expected_delivery": _extract_expected_delivery(order),
        }
        if tracking:
            return (
                f"shared the current tracking status — tracking number {tracking}, "
                f"status {status}",
                facts,
            )
        return f"shared the current status of your order ({status})", facts

    if state.category == "refund_status_check":
        facts = {
            "order_id": order.get("order_id", ""),
            "refund_status": order.get("refund_status") or "pending",
        }
        return (
            "confirmed your refund is processed; it should appear in 5–7 business days",
            facts,
        )

    if state.category == "policy_question":
        kb = state.cache.get("kb") or {}
        facts = {"kb_snippets": kb.get("snippets", [])[:4]}
        return "explained the return windows for electronics and how exchanges work", facts

    if state.category == "return_request":
        elig = state.cache.get("eligibility") or {}
        if elig.get("vip_override"):
            facts = {
                "order_id": order.get("order_id", ""),
                "note": "VIP pre-approved extended-return exception applied",
            }
            return (
                "approved your return as a VIP pre-approved exception — we'll email "
                "return instructions shortly",
                facts,
            )
        if elig.get("eligible"):
            if policies.is_tentative_return(ticket_text):
                facts = {
                    "order_id": order.get("order_id", ""),
                    "return_deadline": order.get("return_deadline", ""),
                    "no_action_started": True,
                }
                return (
                    "confirmed the return window and explained the process — no return "
                    "has been started yet",
                    facts,
                )
            return "confirmed your return is eligible and explained next steps", facts
        return (
            "reviewed your return — the window has expired, but here are alternatives",
            facts,
        )

    if state.category == "ambiguous":
        return (
            "asked three targeted questions so we can locate and help with the right "
            "order: (1) your order ID, (2) the product name, (3) a brief description "
            "of what's going wrong",
            {"questions": [
                "What is your order ID (it starts with ORD-)?",
                "Which product is this about?",
                "What specifically isn't working as expected?",
            ]},
        )

    # No category branch matched — log so the gap is observable in trace + audit
    state.log(
        "warn",
        f"_summary_for_readonly: no handler for category {state.category!r}; "
        "returning generic reply — add a branch here to handle it properly",
    )
    return "reviewed your request", facts


def _extract_expected_delivery(order: dict[str, Any]) -> str:
    import re as _re

    m = _re.search(r"expected delivery\s+(\d{4}-\d{2}-\d{2})", (order.get("notes") or "").lower())
    return m.group(1) if m else ""


async def _escalate(state: TicketState) -> None:
    summary = policies.compute_escalation_reason(state)
    priority = policies.escalation_priority(state)
    try:
        await call_tool(
            "escalate",
            state,
            ticket_id=state.ticket.ticket_id,
            summary=summary,
            priority=priority,
        )
    except RegistryError:
        state.log("escalate_error", "escalate tool failed")
    state.escalation_summary = summary
    state.escalation_brief = policies.compute_escalation_brief(state)
    state.outcome = "escalated"
    state.log("escalate", f"priority={priority} — {summary[:120]}")

    context: dict[str, Any] = {"outcome": "escalated"}
    # When the reason we can't act is "we don't know what order you mean",
    # the customer-facing reply must say that explicitly — not a generic
    # "a specialist will follow up" boilerplate.
    if "order_missing" in state.cache:
        context["facts"] = {
            "questions": [
                f"The order ID you mentioned ({state.cache['order_missing']}) "
                "is not in our system — could you double-check the order "
                "confirmation email and share the correct ID?",
                "If you prefer, reply with the email address you used at "
                "checkout and we can look it up for you.",
            ],
        }
    elif state.cache.get("conflict"):
        context["facts"] = {
            "questions": [
                "Could you share the order ID from your confirmation email "
                "so we can make sure we're looking at the right order?",
            ],
        }

    try:
        reply = await llm.draft_reply(state, context)
        await call_tool(
            "send_reply", state, ticket_id=state.ticket.ticket_id, message=reply.message
        )
        state.reply_sent = reply.message
    except RegistryError:
        pass


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}_{uuid.uuid4().hex[:6]}"
