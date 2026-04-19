"""CLI-level subprocess test for --archive semantics.

Proves that a chaos rerun through `run.py --archive` diverts the audit log
to `runs/<run_id>.json` and leaves the clean `audit_log.json` untouched.
This is the contract behind `scripts/demo.sh --chaos 0.15 --seed 42`:
the reviewer-facing submission log must survive any chaos experiment.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Copy the project into tmp_path so the test can safely run CLI side-effects."""
    copy_root = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT,
        copy_root,
        ignore=shutil.ignore_patterns(
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "runs",
            "audit_log.json",
            "dead_letter_queue.json",
            "*.pyc",
        ),
    )
    return copy_root


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Force rules mode regardless of ambient .env
    env["MODE"] = "rules"
    return subprocess.run(
        [sys.executable, "run.py", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_archive_diverts_and_leaves_clean_log_untouched(sandbox: Path) -> None:
    # Step 1: clean run writes audit_log.json
    clean = _run_cli(sandbox, "--mode", "rules", "--chaos", "0", "--seed", "42")
    assert clean.returncode == 0, clean.stderr
    clean_log = sandbox / "audit_log.json"
    assert clean_log.exists(), "baseline audit_log.json should exist after clean run"
    baseline_bytes = clean_log.read_bytes()
    baseline_entries = json.loads(baseline_bytes)
    assert len(baseline_entries) == 20

    # Step 2: chaos rerun with --archive must divert to runs/
    chaos = _run_cli(
        sandbox, "--mode", "rules", "--chaos", "0.15", "--seed", "42", "--archive"
    )
    assert chaos.returncode == 0, chaos.stderr

    runs_dir = sandbox / "runs"
    assert runs_dir.is_dir(), "runs/ should be created by --archive"
    run_files = sorted(runs_dir.glob("run_*.json"))
    assert len(run_files) == 1, f"expected exactly one archived run, got {run_files}"
    archived = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert len(archived) == 20

    # The clean log on disk must be byte-identical to the baseline
    assert clean_log.read_bytes() == baseline_bytes, (
        "audit_log.json was modified by a --archive chaos rerun — "
        "the divert semantics are broken"
    )

    # And the archived run must itself show chaos happened (at least one recovery
    # attempt or unrecovered failure across the 20 tickets at chaos=0.15 seed=42).
    saw_failure = any(entry.get("failures") for entry in archived)
    assert saw_failure, "chaos rerun produced no failures — seed may be wrong"


def _normalise(entries: list[dict]) -> list[dict]:
    """Strip wallclock/run fields so two runs can be compared structurally."""
    out = []
    for e in entries:
        copy = {k: v for k, v in e.items() if k not in ("timestamp", "run_id", "duration_ms")}
        out.append(copy)
    return out


def test_chaos_is_reproducible_across_fresh_interpreters(sandbox: Path) -> None:
    """Two separate `python run.py` processes with the same seed must produce
    byte-identical outcomes (modulo timestamps). This is why we replaced
    Python's salted str.__hash__ with SHA-256 seeding in app/failures.py —
    without it, chaos would be non-deterministic across PYTHONHASHSEED values.
    """
    first = _run_cli(
        sandbox,
        "--mode", "rules", "--chaos", "0.15", "--seed", "42",
        "--audit-out", "run_a.json",
    )
    assert first.returncode == 0, first.stderr

    second = _run_cli(
        sandbox,
        "--mode", "rules", "--chaos", "0.15", "--seed", "42",
        "--audit-out", "run_b.json",
    )
    assert second.returncode == 0, second.stderr

    a = json.loads((sandbox / "run_a.json").read_text(encoding="utf-8"))
    b = json.loads((sandbox / "run_b.json").read_text(encoding="utf-8"))

    assert _normalise(a) == _normalise(b), (
        "Two fresh-interpreter chaos runs diverged — the SHA-256 seeding in "
        "app/failures._rng has regressed."
    )
