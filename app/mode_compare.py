"""Compare rules, hybrid, and llm runs over the same ticket set."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.agent import new_run_id, process_ticket
from app.config import CONFIG
from app.models import AuditEntry, Ticket
from app.tools import _IDEMPOTENCY, _ORDER_LOCKS
from app.llm import _CLASSIFY_CACHE

EXPECTATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "ticket_expectations.json"


def load_tickets(path: str | None = None) -> list[Ticket]:
    tickets_path = Path(path or CONFIG.tickets_path)
    raw = json.loads(tickets_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "tickets" in raw:
        raw = raw["tickets"]
    return [Ticket.model_validate(item) for item in raw]


def load_expectations(path: str | None = None) -> dict[str, dict[str, Any]]:
    expectations_path = Path(path) if path else EXPECTATIONS_PATH
    if not expectations_path.exists():
        return {}
    raw = json.loads(expectations_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return {str(ticket_id): payload for ticket_id, payload in raw.items()}


async def run_mode(mode: str, tickets: list[Ticket]) -> list[AuditEntry]:
    original_mode = CONFIG.mode
    original_chaos = CONFIG.chaos_rate
    try:
        object.__setattr__(CONFIG, "mode", mode)
        object.__setattr__(CONFIG, "chaos_rate", 0.0)
        _IDEMPOTENCY.clear()
        _ORDER_LOCKS.clear()
        _CLASSIFY_CACHE.clear()
        run_id = f"{new_run_id()}_{mode}"
        return [await process_ticket(ticket, run_id=run_id) for ticket in tickets]
    finally:
        object.__setattr__(CONFIG, "mode", original_mode)
        object.__setattr__(CONFIG, "chaos_rate", original_chaos)


async def compare_modes(tickets: list[Ticket] | None = None) -> dict[str, Any]:
    tickets = tickets or load_tickets()
    results_by_mode: dict[str, list[AuditEntry]] = {}
    for mode in ("rules", "hybrid", "llm"):
        results_by_mode[mode] = await run_mode(mode, tickets)
    return build_report(results_by_mode, expectations=load_expectations())


def _reply_matches(reply: str, expected_substrings: list[str]) -> bool:
    low = (reply or "").lower()
    return all(part.lower() in low for part in expected_substrings)


def build_report(
    results_by_mode: dict[str, list[AuditEntry]],
    *,
    expectations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    per_mode: dict[str, dict[str, Any]] = {}
    by_mode_id: dict[str, dict[str, AuditEntry]] = {}
    expectations = expectations or {}

    for mode, entries in results_by_mode.items():
        by_mode_id[mode] = {entry.ticket_id: entry for entry in entries}
        outcomes = Counter(entry.outcome for entry in entries)
        basis = Counter(entry.decision_basis for entry in entries)
        per_mode[mode] = {
            "tickets": len(entries),
            "resolved": outcomes.get("resolved", 0),
            "declined": outcomes.get("declined", 0),
            "escalated": outcomes.get("escalated", 0),
            "avg_classifier_confidence": round(
                sum(
                    (
                        entry.classifier_confidence
                        if entry.classifier_confidence is not None
                        else entry.confidence
                    )
                    for entry in entries
                )
                / max(len(entries), 1),
                3,
            ),
            "avg_evidence_confidence": round(
                sum(
                    (
                        entry.evidence_confidence
                        if entry.evidence_confidence is not None
                        else entry.confidence
                    )
                    for entry in entries
                )
                / max(len(entries), 1),
                3,
            ),
            "avg_action_confidence": round(
                sum(
                    (
                        entry.action_confidence
                        if entry.action_confidence is not None
                        else entry.confidence
                    )
                    for entry in entries
                )
                / max(len(entries), 1),
                3,
            ),
            "decision_basis": dict(sorted(basis.items())),
        }
        if expectations:
            matched = 0
            mismatches: list[dict[str, Any]] = []
            for entry in entries:
                expected = expectations.get(entry.ticket_id)
                if expected is None:
                    continue
                reasons: list[str] = []
                if expected.get("category") and entry.category != expected["category"]:
                    reasons.append(f"category expected {expected['category']} got {entry.category}")
                if expected.get("outcome") and entry.outcome != expected["outcome"]:
                    reasons.append(f"outcome expected {expected['outcome']} got {entry.outcome}")
                if expected.get("decision_basis") and entry.decision_basis != expected["decision_basis"]:
                    reasons.append(
                        "decision_basis expected "
                        f"{expected['decision_basis']} got {entry.decision_basis}"
                    )
                reply_contains = expected.get("reply_contains") or []
                if reply_contains and not _reply_matches(entry.reply_sent or "", reply_contains):
                    reasons.append(f"reply missing {reply_contains}")
                if reasons:
                    mismatches.append(
                        {
                            "ticket_id": entry.ticket_id,
                            "reasons": reasons,
                            "reply": entry.reply_sent or "",
                        }
                    )
                else:
                    matched += 1
            total = len(expectations)
            per_mode[mode]["gold_matched"] = matched
            per_mode[mode]["gold_total"] = total
            per_mode[mode]["gold_match_rate"] = round(matched / max(total, 1), 3)
            per_mode[mode]["gold_mismatches"] = mismatches

    ticket_ids = sorted(
        {
            ticket_id
            for entries in results_by_mode.values()
            for ticket_id in (entry.ticket_id for entry in entries)
        }
    )

    diffs: list[dict[str, Any]] = []
    all_tickets: list[dict[str, Any]] = []
    for ticket_id in ticket_ids:
        modes: dict[str, dict[str, Any]] = {}
        signatures: set[tuple[str, str, str, str]] = set()
        for mode, entry_map in by_mode_id.items():
            entry = entry_map[ticket_id]
            modes[mode] = {
                "category": entry.category,
                "outcome": entry.outcome,
                "decision_basis": entry.decision_basis,
                "classifier_confidence": (
                    entry.classifier_confidence
                    if entry.classifier_confidence is not None
                    else entry.confidence
                ),
                "evidence_confidence": (
                    entry.evidence_confidence
                    if entry.evidence_confidence is not None
                    else entry.confidence
                ),
                "action_confidence": (
                    entry.action_confidence
                    if entry.action_confidence is not None
                    else entry.confidence
                ),
                "escalation_summary": entry.escalation_summary or "",
            }
            signatures.add(
                (
                    entry.category,
                    entry.outcome,
                    entry.decision_basis,
                    entry.escalation_summary or "",
                )
            )
        item: dict[str, Any] = {"ticket_id": ticket_id, "modes": modes}
        if expectations.get(ticket_id):
            item["expected"] = expectations[ticket_id]
        if len(signatures) > 1:
            diffs.append(item)
        all_tickets.append(item)

    return {
        "summary": per_mode,
        "diff_count": len(diffs),
        "tickets_with_differences": diffs,
        "tickets": all_tickets,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = ["Mode comparison", ""]
    for mode, summary in report["summary"].items():
        lines.append(
            f"{mode:<6} resolved={summary['resolved']:>2} "
            f"declined={summary['declined']:>2} escalated={summary['escalated']:>2} "
            f"classifier={summary['avg_classifier_confidence']:.3f} "
            f"evidence={summary['avg_evidence_confidence']:.3f} "
            f"action={summary['avg_action_confidence']:.3f}"
        )
        if "gold_match_rate" in summary:
            lines[-1] += (
                f" gold={summary['gold_matched']}/{summary['gold_total']} "
                f"({summary['gold_match_rate']:.3f})"
            )
    lines.append("")
    lines.append(f"tickets with differences: {report['diff_count']}")
    for item in report["tickets_with_differences"]:
        lines.append(f"  {item['ticket_id']}")
        for mode, payload in item["modes"].items():
            lines.append(
                "    "
                f"{mode:<6} {payload['outcome']:<9} {payload['decision_basis']:<24} "
                f"cat={payload['category']:<20} "
                f"cls={payload['classifier_confidence']:.2f} "
                f"ev={payload['evidence_confidence']:.2f} "
                f"act={payload['action_confidence']:.2f}"
            )
    gold_mismatches = {
        mode: summary.get("gold_mismatches", [])
        for mode, summary in report["summary"].items()
        if summary.get("gold_mismatches")
    }
    if gold_mismatches:
        lines.append("")
        lines.append("gold-set mismatches")
        for mode, mismatches in gold_mismatches.items():
            lines.append(f"  {mode}")
            for mismatch in mismatches:
                lines.append(
                    f"    {mismatch['ticket_id']}: " + "; ".join(mismatch["reasons"])
                )
    return "\n".join(lines)
