#!/usr/bin/env python3
"""Pretty-print one ticket from the most recent archived run in runs/."""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.show_ticket import _load_rows, _show


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show one ticket from the latest archived run")
    parser.add_argument("ticket_id", help="ticket id, e.g. TKT-011")
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="omit reasoning_trace for a shorter, demo-friendly view",
    )
    return parser


def _latest_run() -> Path:
    runs = sorted(glob.glob(str(ROOT / "runs" / "*.json")), key=os.path.getmtime)
    if not runs:
        print("[error] no archived runs found under runs/", file=sys.stderr)
        raise SystemExit(2)
    return Path(runs[-1])


def main() -> int:
    args = build_parser().parse_args()
    path = _latest_run()
    rows = _load_rows(path)
    match = next((row for row in rows if row.get("ticket_id") == args.ticket_id), None)
    if match is None:
        print(f"[error] ticket {args.ticket_id} not found in {path}", file=sys.stderr)
        return 2
    _show(match, path=path, show_trace=not args.no_trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
