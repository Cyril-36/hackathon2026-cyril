"""Adapter: AuditEntry + Ticket fixtures + Event -> frontend wire format.

This is the only place the snake_case agent schema gets translated to the
frontend's shape. Keep all translation logic here so future schema shifts
don't leak into the server or components.

Design rules (from the plan):
- Snapshot mode degrades gracefully: trace rows are built from
  reasoning_trace + tools_used + failures. No synthesized args/result.
- Live mode feeds richer events through adapt_event; the frontend's reducer
  mutates trace rows in place, so the live path never touches this module.
- decision_basis values stay raw — the UI maps them via BASIS_LABEL.
- Tokens / policy_version are honest nulls — the rules-mode agent doesn't
  track them, and we won't fake them.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import CONFIG
from app.events import Event
from app.models import AuditEntry, Customer, Failure, Ticket


# ---- Static tool registry metadata --------------------------------------


TOOL_META = [
    {"id": "get_order", "kind": "read", "avg_ms": 42},
    {"id": "get_customer", "kind": "read", "avg_ms": 38},
    {"id": "get_customer_orders", "kind": "read", "avg_ms": 61},
    {"id": "get_product", "kind": "read", "avg_ms": 35},
    {"id": "search_knowledge_base", "kind": "read", "avg_ms": 180},
    {"id": "check_refund_eligibility", "kind": "read", "avg_ms": 54},
    {"id": "issue_refund", "kind": "write", "avg_ms": 210},
    {"id": "cancel_order", "kind": "write", "avg_ms": 140},
    {"id": "initiate_exchange", "kind": "write", "avg_ms": 190},
    {"id": "send_reply", "kind": "write", "avg_ms": 88},
    {"id": "escalate", "kind": "write", "avg_ms": 22},
]


URGENCY_TO_PRIORITY = {"urgent": "P1", "high": "P1", "medium": "P2", "normal": "P2", "low": "P3"}


def _model_label(mode: str) -> str:
    if mode == "rules":
        return "rules-deterministic"
    provider = CONFIG.llm_provider
    if mode == "hybrid":
        if provider == "groq":
            return f"rules + {CONFIG.groq_model}"
        if provider == "ollama":
            return f"rules + {CONFIG.ollama_model}"
        return f"rules + {provider}"
    if provider == "groq":
        return CONFIG.groq_model
    if provider == "ollama":
        return CONFIG.ollama_model
    return provider


# ---- Public entry points -------------------------------------------------


def load_fixtures() -> dict[str, Ticket]:
    path = Path(CONFIG.tickets_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "tickets" in raw:
        raw = raw["tickets"]
    by_id: dict[str, Ticket] = {}
    for item in raw:
        try:
            t = Ticket.model_validate(item)
            by_id[t.ticket_id] = t
        except Exception:
            pass
    return by_id


@lru_cache(maxsize=1)
def _customer_fixture_index() -> dict[str, Customer]:
    path = Path(CONFIG.customers_path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_email: dict[str, Customer] = {}
    for item in raw:
        try:
            customer = Customer.model_validate(item)
        except Exception:
            continue
        by_email[customer.email] = customer
    return by_email


def load_snapshot(run_id: str | None = None) -> dict[str, Any]:
    """Read an audit artifact (clean or archived web run) + fixtures, adapt.

    `run_id` routing:
      • None      → `audit_log.json` (the clean submission artifact)
      • "latest"  → most recent `runs/*.json` by mtime; falls back to clean
      • "<id>"    → `runs/<id>.json`; falls back to clean if missing
    """
    fixtures = load_fixtures()
    audit_path = _resolve_audit_path(run_id)
    entries: list[AuditEntry] = []
    if audit_path and audit_path.exists():
        try:
            raw = json.loads(audit_path.read_text(encoding="utf-8"))
            for item in raw:
                try:
                    entries.append(AuditEntry.model_validate(item))
                except Exception:
                    pass
        except Exception:
            entries = []
    return adapt_audit_to_frontend(entries, fixtures, run_id_hint=run_id)


def _resolve_audit_path(run_id: str | None) -> Path:
    """Map a `run_id` argument to a concrete audit JSON path."""
    clean = Path(CONFIG.audit_log_path)
    if run_id is None:
        return clean
    runs_dir = clean.parent / "runs"
    if run_id == "latest":
        if not runs_dir.is_dir():
            return clean
        candidates = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else clean
    # Specific archived run; fall back to clean if it doesn't exist so the
    # UI never gets a hard 500 on a stale localStorage id.
    candidate = runs_dir / f"{run_id}.json"
    return candidate if candidate.exists() else clean


def adapt_audit_to_frontend(
    entries: list[AuditEntry],
    fixtures: dict[str, Ticket],
    *,
    run_id_hint: str | None = None,
) -> dict[str, Any]:
    """Convert stored audit entries + fixtures into the frontend payload shape."""
    ordered_ids = list(fixtures.keys())
    entry_by_id = {e.ticket_id: e for e in entries}

    tickets: list[dict[str, Any]] = []
    for tid in ordered_ids:
        fx = fixtures[tid]
        entry = entry_by_id.get(tid)
        tickets.append(_ticket_from_fixture(fx, entry))

    run_id = entries[0].run_id if entries else ""
    mode = entries[0].mode if entries else CONFIG.mode

    meta = {
        "run_id": run_id,
        "started_at": entries[0].timestamp if entries else "",
        "ended_at": entries[-1].timestamp if entries else "",
        "duration_ms": sum(e.duration_ms for e in entries),
        "mode": mode,
        "chaos": _infer_chaos(entries, run_id_hint),
        "concurrency": CONFIG.max_concurrent_tickets,
        "model": _model_label(mode),
        "policy_version": "kb-v1.0",
        "tool_registry_version": "tools-v1.0",
    }

    stats = _compute_stats(tickets)
    return {"meta": meta, "tools": TOOL_META, "tickets": tickets, "stats": stats}


def _infer_chaos(entries: list[AuditEntry], run_id_hint: str | None) -> bool:
    """Best-effort chaos flag for snapshot views.

    `audit_log.json` is the clean submission artifact, so treat it as
    non-chaos. Archived web runs do not currently carry a separate
    run-level metadata header, so infer chaos from the effects it leaves
    behind: recovery, unrecovered failures, or explicit tool-failure bases.
    """
    if run_id_hint is None:
        return False
    return any(
        entry.recovery_attempted
        or bool(entry.failures)
        or entry.decision_basis in {"tool_failure", "recovered_and_resolved"}
        for entry in entries
    )


def adapt_ticket_start(ticket_id: str, fixture: Ticket | None) -> dict[str, Any]:
    """Pre-run placeholder ticket — rendered before the first audit entry."""
    if fixture is None:
        return {"id": ticket_id, "outcome": "pending", "trace": []}
    return _ticket_from_fixture(fixture, entry=None)


# ---- Internals -----------------------------------------------------------


def _ticket_from_fixture(fx: Ticket, entry: AuditEntry | None) -> dict[str, Any]:
    base = {
        "id": fx.ticket_id,
        "received_at": fx.created_at,
        "subject": fx.subject,
        "body": fx.body,
        "source": fx.source,
        "customer": _customer_from_email(fx.customer_email, fx.tier),
        "expected_action": fx.expected_action,
        # Order id parsed from body if possible (best-effort, cosmetic).
        "order_id": _extract_order_id(fx.body) or _extract_order_id(fx.subject) or "",
    }

    if entry is None:
        # Snapshot mode with no prior run — show the ticket as unprocessed.
        base.update({
            "priority": "P3",
            "urgency": "low",
            "category": "pending",
            "classified_confidence": 0.0,
            "evidence_confidence": 0.0,
            "outcome": "pending",
            "decision_basis": "pending",
            "agent_confidence": 0.0,
            "auto_replied": False,
            "duration_ms": 0,
            "tokens": {"in": None, "out": None},
            "trace": [],
            "failures": [],
            "tools_used": [],
            "recovery_attempted": False,
            "reply": None,
            "escalation_summary": None,
        })
        return base

    base.update({
        "priority": URGENCY_TO_PRIORITY.get(entry.urgency, "P3"),
        "urgency": entry.urgency,
        "category": entry.category,
        "classified_confidence": (
            entry.classifier_confidence
            if entry.classifier_confidence is not None
            else entry.confidence
        ),
        "evidence_confidence": (
            entry.evidence_confidence
            if entry.evidence_confidence is not None
            else entry.confidence
        ),
        "outcome": entry.outcome,
        "decision_basis": entry.decision_basis,
        "agent_confidence": (
            entry.action_confidence
            if entry.action_confidence is not None
            else entry.confidence
        ),
        "auto_replied": bool(entry.reply_sent),
        "duration_ms": entry.duration_ms,
        "tokens": {"in": None, "out": None},
        "trace": _trace_from_audit(entry),
        "failures": [f.model_dump() for f in entry.failures],
        "tools_used": list(entry.tools_used),
        "recovery_attempted": entry.recovery_attempted,
        "reply": entry.reply_sent,
        "escalation_summary": entry.escalation_summary,
    })
    return base


def _customer_from_email(email: str, tier: int | None) -> dict[str, Any]:
    customer = _customer_fixture_index().get(email)
    if customer is not None:
        return {
            "id": customer.customer_id,
            "name": customer.name,
            "email": customer.email,
            "tier": customer.tier,
            "prior_tickets": 0,
        }

    # Best-effort name from local-part of the email
    local = email.split("@")[0] if email else "customer"
    name_parts = [p for p in local.replace(".", " ").replace("_", " ").split() if p]
    name = " ".join(p.capitalize() for p in name_parts) or "Customer"
    tier_label = "premium" if (tier or 0) >= 2 else "standard"
    stable_id = ""
    if email:
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        stable_id = f"CUST-{int(digest[:8], 16) % 9999:04d}"
    return {
        "id": stable_id,
        "name": name,
        "email": email,
        "tier": tier_label,
        "prior_tickets": 0,
    }


def _trace_from_audit(entry: AuditEntry) -> list[dict[str, Any]]:
    """Best-effort trace rebuild from reasoning_trace + tools_used + failures.

    We don't have per-step timing or tool args in the audit log, so every
    row gets `t=0, ms=0` (the frontend tolerates this — it hides the `+Xms`
    prefix when timing is zero). Live mode fills in real timing through the
    event stream.
    """
    rows: list[dict[str, Any]] = []
    tool_idx = 0
    failure_errors = {f.tool: f for f in entry.failures}

    for step in entry.reasoning_trace:
        kind = _kind_for_step(step.step)
        row: dict[str, Any] = {
            "t": 0,
            "ms": 0,
            "kind": kind,
            "label": step.step,
            "note": step.note,
            "status": "ok",
        }
        if kind == "tool" and tool_idx < len(entry.tools_used):
            row["tool"] = entry.tools_used[tool_idx]
            tool_idx += 1
        rows.append(row)

    # Append recovery rows for recovered failures (best-effort)
    for f in entry.failures:
        rows.append({
            "t": 0,
            "ms": 0,
            "kind": "recover" if f.recovered else "tool",
            "tool": f.tool,
            "status": "recovered" if f.recovered else "error",
            "error": f.error,
            "retry_count": f.retry_count,
            "note": (
                f"recovered after {f.retry_count} retries"
                if f.recovered
                else f"unrecovered: {f.error}"
            ),
        })

    _ = failure_errors  # silence unused
    return rows


def _kind_for_step(step_name: str) -> str:
    if step_name in ("classify",):
        return "classify"
    if step_name in ("act",):
        return "tool"
    if step_name in ("plan", "evaluate", "verify", "verify_result", "decide"):
        return "decide"
    if step_name in ("resolve",):
        return "decide"
    if step_name in ("escalate", "escalate_error", "resolve_failed"):
        return "decide"
    return "decide"


def _extract_order_id(text: str) -> str | None:
    import re

    m = re.search(r"\bORD-\d{3,}\b", text)
    return m.group(0) if m else None


def _compute_stats(tickets: list[dict[str, Any]]) -> dict[str, Any]:
    def by(pred):
        return sum(1 for t in tickets if pred(t))

    total = len(tickets)
    basis_counter: dict[str, int] = {}
    for t in tickets:
        b = t.get("decision_basis") or "pending"
        basis_counter[b] = basis_counter.get(b, 0) + 1

    processed = [t for t in tickets if t.get("outcome") != "pending"]
    conf_sum = sum(t.get("agent_confidence") or 0 for t in processed)
    avg_conf = (conf_sum / len(processed)) if processed else 0.0

    return {
        "total": total,
        "resolved": by(lambda t: t["outcome"] == "resolved"),
        "escalated": by(lambda t: t["outcome"] == "escalated"),
        "declined": by(lambda t: t["outcome"] == "declined"),
        "dlq": by(lambda t: any(not f.get("recovered") for f in (t.get("failures") or []))),
        "failed": by(lambda t: bool(t.get("failures"))),
        "recovered": by(lambda t: t.get("recovery_attempted")),
        "avg_confidence": round(avg_conf, 3),
        "by_basis": basis_counter,
        "tokens_in": sum(int(t.get("tokens_in") or 0) for t in tickets),
        "tokens_out": sum(int(t.get("tokens_out") or 0) for t in tickets),
        "tool_calls": sum(len(t.get("tools_used") or []) for t in tickets),
    }


# ---- Live event adaptation ----------------------------------------------


def adapt_event(ev: Event) -> dict[str, Any]:
    """Shape an Event for SSE wire transmission.

    The frontend's reducer (`data.js::applyEvent`) mutates ticket state
    based on the event `type`. We keep the wire format close to Event but
    unwrap payload to top level for easier JS access.
    """
    out: dict[str, Any] = {
        "type": ev.type,
        "run_id": ev.run_id,
        "ticket_id": ev.ticket_id,
        "ts_ms": ev.ts_ms,
        **ev.payload,
    }
    return out
