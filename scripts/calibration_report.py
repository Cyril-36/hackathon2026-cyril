#!/usr/bin/env python3
"""Calibration report: does classifier_confidence correlate with actual accuracy?

Compares the agent's audit log against the structured gold-set in
data/ticket_expectations.json (which has explicit `outcome`, `category`, and
`decision_basis` fields per ticket).  Groups by classifier_confidence decile
and reports three accuracy dimensions per band:

  outcome   — did the agent's resolve/escalate/declined match the gold label?
  category  — did the classifier pick the right intent category?
  basis     — did the decision_basis match the gold label (when specified)?

A well-calibrated classifier should have outcome accuracy ≈ confidence at each
decile. A gap of > ±15% signals the confidence score is systematically over-
or under-stated for that band.

Usage:
    python scripts/calibration_report.py
    python scripts/calibration_report.py --audit audit_log_chaos_seed42.json
    python scripts/calibration_report.py --gold data/ticket_expectations.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = ROOT / "data" / "ticket_expectations.json"
DEFAULT_AUDIT = ROOT / "audit_log.json"


def _load(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Classifier calibration report")
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT),
                        help="Path to audit log JSON")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD),
                        help="Path to ticket_expectations.json gold set")
    args = parser.parse_args()

    audit_path = Path(args.audit)
    gold_path = Path(args.gold)

    if not audit_path.exists():
        print(f"[calibration] audit log not found: {audit_path}")
        return
    if not gold_path.exists():
        print(f"[calibration] gold set not found: {gold_path}")
        return

    entries: list[dict] = _load(audit_path)  # type: ignore[assignment]
    gold: dict[str, dict] = _load(gold_path)  # type: ignore[assignment]

    labelled = [e for e in entries if e["ticket_id"] in gold]
    if not labelled:
        print("[calibration] no audit entries match the gold set — nothing to report")
        return

    # Group by 0.1-wide confidence decile bands
    Bucket = dict[str, int]
    buckets: dict[float, Bucket] = defaultdict(
        lambda: {"n": 0, "outcome_ok": 0, "cat_ok": 0, "basis_ok": 0, "basis_n": 0}
    )

    for e in labelled:
        conf = e.get("classifier_confidence") or e.get("confidence") or 0.0
        decile = round(int(conf * 10) / 10, 1)
        g = gold[e["ticket_id"]]

        b = buckets[decile]
        b["n"] += 1

        if e.get("outcome") == g.get("outcome"):
            b["outcome_ok"] += 1
        if e.get("category") == g.get("category"):
            b["cat_ok"] += 1
        if "decision_basis" in g:
            b["basis_n"] += 1
            if e.get("decision_basis") == g["decision_basis"]:
                b["basis_ok"] += 1

    # ── Report ──────────────────────────────────────────────────────────────
    print(f"\nCalibration report — {audit_path.name}  (gold: {gold_path.name})")
    print(f"Labelled tickets: {len(labelled)} / {len(entries)}\n")

    header = f"{'Conf':>6}  {'N':>4}  {'Outcome':>9}  {'Category':>9}  {'Basis':>9}  {'Outcome gap':>12}"
    print(header)
    print("─" * len(header))

    total_outcome_ok = total_cat_ok = total_basis_ok = total_basis_n = total_n = 0

    for decile in sorted(buckets):
        b = buckets[decile]
        n = b["n"]
        out_acc = b["outcome_ok"] / n
        cat_acc = b["cat_ok"] / n
        bas_acc = b["basis_ok"] / b["basis_n"] if b["basis_n"] else None
        gap = out_acc - decile  # positive = over-confident; negative = under-confident

        bas_str = f"{bas_acc:>7.0%}" if bas_acc is not None else "    n/a"
        gap_str = f"{gap:>+.0%}"
        print(
            f"{decile:.1f}–{decile+0.1:.1f}  {n:>4}  {out_acc:>8.0%}  "
            f"{cat_acc:>8.0%}  {bas_str}  {gap_str:>12}"
        )

        total_n += n
        total_outcome_ok += b["outcome_ok"]
        total_cat_ok += b["cat_ok"]
        total_basis_ok += b["basis_ok"]
        total_basis_n += b["basis_n"]

    print("─" * len(header))
    overall_out = total_outcome_ok / total_n
    overall_cat = total_cat_ok / total_n
    overall_bas = total_basis_ok / total_basis_n if total_basis_n else None
    bas_total_str = f"{overall_bas:>7.0%}" if overall_bas is not None else "    n/a"
    print(
        f"{'TOTAL':>6}  {total_n:>4}  {overall_out:>8.0%}  "
        f"{overall_cat:>8.0%}  {bas_total_str}"
    )
    print()

    # Calibration verdict
    if overall_out >= 0.90:
        verdict = "✓ excellent (≥90% outcome accuracy)"
    elif overall_out >= 0.80:
        verdict = "✓ good (≥80% outcome accuracy)"
    elif overall_out >= 0.70:
        verdict = "~ acceptable (≥70% — review low-confidence bands)"
    else:
        verdict = "✗ poor (<70% — classifier needs tuning)"
    print(f"Verdict: {verdict}")
    print()


if __name__ == "__main__":
    main()
