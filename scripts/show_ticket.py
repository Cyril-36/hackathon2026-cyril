#!/usr/bin/env python3
"""Pretty-print one ticket from an audit artifact.

Usage:
    python scripts/show_ticket.py TKT-001
    python scripts/show_ticket.py TKT-006 --file runs/run_...json
    python scripts/show_ticket.py TKT-001 --no-trace
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show one ticket from an audit log")
    parser.add_argument("ticket_id", help="ticket id, e.g. TKT-001")
    parser.add_argument(
        "--file",
        default=str(ROOT / "audit_log.json"),
        help="audit artifact to read (defaults to audit_log.json)",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="omit reasoning_trace for a shorter, demo-friendly view",
    )
    return parser


def _load_rows(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[error] audit file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as exc:
        print(f"[error] invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(raw, list):
        print(f"[error] expected a JSON list in {path}", file=sys.stderr)
        raise SystemExit(2)
    return raw


def _show(row: dict, *, path: Path, show_trace: bool) -> None:
    print(f"ticket:   {row['ticket_id']}")
    print(f"source:   {path.name}")
    print(
        "result:   "
        f"{row.get('outcome')} | basis={row.get('decision_basis')} | "
        f"category={row.get('category')}"
    )
    if "urgency" in row:
        print(f"urgency:  {row.get('urgency')}")
    if "confidence" in row:
        print(
            "conf:     "
            f"classifier={row.get('classifier_confidence')} "
            f"evidence={row.get('evidence_confidence')} "
            f"action={row.get('action_confidence')}"
        )
    print(f"tools:    {', '.join(row.get('tools_used') or []) or '(none)'}")

    failures = row.get("failures") or []
    if failures:
        print("failures:")
        for f in failures:
            print(
                "  - "
                f"{f.get('tool')} {f.get('error')} "
                f"(recovered={f.get('recovered')}, retries={f.get('retry_count', 0)})"
            )

    if row.get("reply_sent"):
        print("reply:")
        print(f"  {row['reply_sent']}")

    if row.get("escalation_summary"):
        print("summary:")
        print(f"  {row['escalation_summary']}")

    if row.get("escalation_brief"):
        print("brief:")
        print(f"  {row['escalation_brief']}")

    if show_trace:
        trace = row.get("reasoning_trace") or []
        if trace:
            print("trace:")
            for step in trace:
                print(f"  {step.get('step')} - {step.get('note')}")


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.file)
    if not path.is_absolute():
        path = ROOT / path

    rows = _load_rows(path)
    match = next((row for row in rows if row.get("ticket_id") == args.ticket_id), None)
    if match is None:
        print(f"[error] ticket {args.ticket_id} not found in {path}", file=sys.stderr)
        return 2

    _show(match, path=path, show_trace=not args.no_trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
