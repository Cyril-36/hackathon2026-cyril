#!/usr/bin/env python3
"""Run the same ticket set through rules, hybrid, and llm, then diff results."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.mode_compare import compare_modes, render_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare rules, hybrid, and llm runs")
    parser.add_argument(
        "--out",
        type=str,
        default=str(ROOT / "runs" / "mode_compare_latest.json"),
        help="where to write the JSON diff report",
    )
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    report = await compare_modes()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(render_report(report))
    print()
    print(f"report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
