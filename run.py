"""ShopWave Autonomous Support Resolution Agent — CLI entry point.

Usage:
    python run.py                          # hybrid mode, all 20 tickets
    python run.py --mode rules             # offline deterministic run
    python run.py --mode llm               # full LLM-driven run
    python run.py --ticket TKT-003         # single ticket
    python run.py --chaos 0.25             # crank up failure rate
    python run.py --today 2024-03-15       # pin the "now" date

All CLI flags are applied to os.environ BEFORE any app module is imported,
so they actually override the frozen Config dataclass (which reads env at
module-import time).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ShopWave Autonomous Support Agent")
    p.add_argument("--mode", choices=["rules", "llm", "hybrid"], default=None)
    p.add_argument("--chaos", type=float, default=None, help="0.0-1.0 failure rate")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--ticket", type=str, default=None, help="single ticket id")
    p.add_argument("--today", type=str, default=None, help="YYYY-MM-DD pin for return-window math")
    p.add_argument(
        "--audit-out",
        type=str,
        default=None,
        help="path for audit_log output; defaults to audit_log.json",
    )
    p.add_argument(
        "--archive",
        action="store_true",
        help="divert audit output to runs/<run_id>.json instead of audit_log.json (keeps the clean submission log untouched for chaos experiments)",
    )
    return p


def _apply_env_overrides(args: argparse.Namespace) -> None:
    """Mutate os.environ BEFORE any app.* import so Config picks up CLI flags."""
    if args.mode:
        os.environ["MODE"] = args.mode
    if args.chaos is not None:
        os.environ["CHAOS"] = str(args.chaos)
    if args.seed is not None:
        os.environ["SEED"] = str(args.seed)
    if args.today:
        os.environ["TODAY"] = args.today


async def main_async(args: argparse.Namespace) -> int:
    # Lazy imports so env overrides above are honoured by CONFIG at load time.
    from datetime import datetime, timezone

    from app.agent import new_run_id, process_ticket
    from app.config import CONFIG
    from app.models import AuditEntry, Failure, Ticket
    from app.state import TicketState

    # Clear DLQ for this run
    if Path(CONFIG.dlq_path).exists():
        Path(CONFIG.dlq_path).unlink()

    tickets = _load_tickets(CONFIG.tickets_path, Ticket)
    if args.ticket:
        tickets = [t for t in tickets if t.ticket_id == args.ticket]
        if not tickets:
            print(f"[error] ticket {args.ticket} not found")
            return 2

    print(
        f"[run] mode={CONFIG.mode} provider={CONFIG.llm_provider} "
        f"tickets={len(tickets)} chaos={CONFIG.chaos_rate} "
        f"today={CONFIG.today} concurrency={CONFIG.max_concurrent_tickets}"
    )

    run_id = new_run_id()
    sem = asyncio.Semaphore(CONFIG.max_concurrent_tickets)

    async def _run_one(t: Ticket) -> AuditEntry:
        async with sem:
            try:
                return await process_ticket(t, run_id=run_id)
            except Exception as exc:  # defensive: never let the runner die
                print(f"[ERROR] ticket {t.ticket_id} crashed: {exc!r}")
                st = TicketState(ticket=t)
                st.failures.append(Failure(tool="agent", error=str(exc)[:120], recovered=False))
                st.outcome = "escalated"
                st.confidence = 0.0
                return AuditEntry(
                    run_id=run_id,
                    ticket_id=t.ticket_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    mode=CONFIG.mode,
                    category="ambiguous",
                    urgency="low",
                    outcome="escalated",
                    confidence=0.0,
                    classifier_confidence=0.0,
                    evidence_confidence=0.0,
                    action_confidence=0.0,
                    tools_used=[],
                    failures=st.failures,
                    recovery_attempted=False,
                    decision_basis="tool_failure",
                    duration_ms=0,
                    reasoning_trace=[],
                    expected_action=t.expected_action,
                    escalation_summary=f"agent crash: {exc}",
                )

    results: list[AuditEntry] = await asyncio.gather(*(_run_one(t) for t in tickets))

    out = [entry.model_dump(mode="json") for entry in results]

    # --audit-out wins. Otherwise --archive diverts to runs/<run_id>.json
    # so chaos experiments don't silently overwrite the clean submission log.
    if args.audit_out:
        audit_path = Path(args.audit_out)
    elif args.archive:
        archive_dir = Path(__file__).resolve().parent / "runs"
        archive_dir.mkdir(exist_ok=True)
        audit_path = archive_dir / f"{run_id}.json"
    else:
        audit_path = Path(CONFIG.audit_log_path)
    audit_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    _print_summary(results, str(audit_path))
    return 0


def _load_tickets(path: str, TicketModel) -> list:
    """Load tickets. Tries native format, falls back to wrapped shape."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "tickets" in raw:
        raw = raw["tickets"]
    tickets = []
    for item in raw:
        try:
            tickets.append(TicketModel.model_validate(item))
        except Exception as exc:  # pragma: no cover
            print(f"[warn] skipping ticket, bad schema: {exc}")
    return tickets


def _print_summary(results: list, path: str) -> None:
    from collections import Counter

    resolved = sum(1 for r in results if r.outcome == "resolved")
    escalated = sum(1 for r in results if r.outcome == "escalated")
    declined = sum(1 for r in results if r.outcome == "declined")
    with_recovery = sum(1 for r in results if r.recovery_attempted)
    avg_classifier_conf = sum(
        (r.classifier_confidence if r.classifier_confidence is not None else r.confidence)
        for r in results
    ) / max(len(results), 1)
    avg_evidence_conf = sum(
        (r.evidence_confidence if r.evidence_confidence is not None else r.confidence)
        for r in results
    ) / max(len(results), 1)
    avg_action_conf = sum(
        (r.action_confidence if r.action_confidence is not None else r.confidence)
        for r in results
    ) / max(len(results), 1)
    basis_counts: Counter[str] = Counter(r.decision_basis for r in results)

    print()
    print("=" * 60)
    print(f"Processed {len(results)} tickets")
    print(f"  resolved : {resolved}")
    print(f"  declined : {declined}")
    print(f"  escalated: {escalated}")
    print(f"  recovery_attempted: {with_recovery}")
    print(f"  avg classifier    : {avg_classifier_conf:.3f}")
    print(f"  avg evidence      : {avg_evidence_conf:.3f}")
    print(f"  avg action        : {avg_action_conf:.3f}")
    print("  decision_basis distribution:")
    for k, v in sorted(basis_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<24} {v}")
    print(f"audit_log -> {path}")


if __name__ == "__main__":
    _args = build_parser().parse_args()
    _apply_env_overrides(_args)
    raise SystemExit(asyncio.run(main_async(_args)))
