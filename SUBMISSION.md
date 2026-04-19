# Submission Guide — ShopWave Support Resolution System

> Agentic AI Hackathon 2026 — one-page, judge-facing index of what's in the
> box, how to run it, and what to expect.

## Deliverables

| Path | Purpose |
|---|---|
| [README.md](README.md) | Full architecture, file guide, rubric notes, reading the audit log |
| [DEMO.md](DEMO.md) | 3-minute live-demo script (4 acts, real commands, no networking) |
| [failure_modes.md](failure_modes.md) | Taxonomy of the 6 failure classes + the evidence for each |
| [architecture.png](architecture.png) | One-page diagram — regenerate with `scripts/gen_architecture.py` |
| [audit_log.json](audit_log.json) | Clean-run artifact (20 tickets, `--mode rules --chaos 0`) |
| [audit_log_chaos_seed42.json](audit_log_chaos_seed42.json) | Deterministic chaos artifact (`--chaos 0.15 --seed 42`) |
| [app/](app) | The orchestrator, registry, policies, tools, model adapter |
| [tests/](tests) | 98 tests across 10 files — orchestration, dataset regression, chaos seed, CLI subprocess, registry/model validation, server and frontend checks |
| [Dockerfile](Dockerfile) | Reproducible container image (`docker build -t shopwave-agent . && docker run --rm shopwave-agent`) |
| [frontend/](frontend) | Optional self-contained React operations console — `./scripts/demo_web.sh` → http://127.0.0.1:8787/ui/ (realtime SSE from the live run path) |
| [app/server.py](app/server.py) | FastAPI backend for the console — only loaded when you run the web UI; CLI is untouched |

## Exact commands to run

```bash
# 0. One-time bootstrap (creates .venv, installs deps)
./scripts/demo.sh

# 1. Full verifier (pytest + clean run + chaos + reproducibility check)
./scripts/verify_submission.sh

# 2. Live demo (see DEMO.md for narration)
./scripts/demo.sh                          # Acts 1–3: clean distribution
./scripts/demo.sh --chaos 0.15 --seed 42   # Act 4:  recovery under chaos

# 3. Single ticket for a focused walk-through
python run.py --mode rules --ticket TKT-004
python run.py --mode rules --ticket TKT-018  # fraud detection
python run.py --mode rules --ticket TKT-007 --chaos 0.15 --seed 42  # stale_data terminal path
```

## Exact outputs to expect

### Clean run (`--chaos 0`)
```
Processed 20 tickets
  resolved : 14
  declined : 1
  escalated: 5
  recovery_attempted: 0
  avg classifier    : 0.864
  avg evidence      : 0.903
  avg action        : 0.877
  decision_basis distribution:
    successful_resolution    14
    policy_guard             3
    low_confidence           2
    fraud_detected           1
```

### Chaos run (`--chaos 0.15 --seed 42`, deterministic)
```
Processed 20 tickets
  resolved : 9
  declined : 1
  escalated: 10
  recovery_attempted: 6
  avg classifier    : 0.864
  avg evidence      : 0.824
  avg action        : 0.783
  decision_basis distribution:
    successful_resolution    8
    tool_failure             6
    policy_guard             2
    low_confidence           2
    recovered_and_resolved   1
    fraud_detected           1
```

### Verifier
```
Submission verification — 5 passed / 0 failed
  PASS  pytest — 98 passed
  PASS  clean run: 14 resolved / 5 escalated / 1 declined
  PASS  chaos rerun diverts (audit_log.json byte-identical pre/post)
  PASS  chaos archive written to runs/run_<…>.json
  PASS  chaos artifact is reproducible (normalised hash matches)
OK: ready to submit.
```

## Design decisions worth pointing to

- **Seven-step loop** with `VERIFY` *and* `EVALUATE` as distinct steps — `app/agent.py`.
- **Dual confidence thresholds** (`0.70` reversible, `0.85` irreversible) in `app/config.py`.
- **DOA still respects irreversible-action order**: damaged-on-arrival uses
  the same `check_refund_eligibility → issue_refund` sequence as every other
  refund. KB §1.5 changes the eligibility decision, not the guardrail.
  See `app/tools.py:check_refund_eligibility`, `app/agent.py:_act`, and
  `app/policies.py:refund_guard`.
- **Per-ticket `effective_today`** (`app/policies.py:effective_today`): return-window
  math uses the ticket's `created_at`, not a global `CONFIG.today`, so stale-window
  tickets (`TKT-002`) are evaluated against the date they were filed.
- **Stable chaos seeding**: `app/failures.py:_rng` uses SHA-256 instead of Python's
  salted `str.__hash__`, which is why `audit_log_chaos_seed42.json` is reproducible
  across interpreter restarts. The verifier proves this.
- **Unified registry** (`app/registry.py`): the same retry/validate/repair pipeline
  wraps tool calls *and* model-assisted calls — one place to read, one place to test.
- **Divert, not duplicate** on `--archive`: chaos reruns land in
  `runs/<run_id>.json` so the clean submission log is never overwritten mid-demo.
- **Chaos-distribution regression test** (`tests/test_chaos_distribution.py`):
  pins the exact `decision_basis` + outcome counters and the "stale_data is
  never recovered" invariant against `audit_log_chaos_seed42.json`, so a silent
  regression in the registry's retry classification will fail CI loudly.
- **Reproducible container** (`Dockerfile`): the same deterministic rules-mode
  pass the verifier uses, runnable with zero host dependencies.

## Scope + non-goals

- No network required for the default `rules` mode. `hybrid` / `llm` modes
  need `GROQ_API_KEY` in `.env`; copy `.env.example` if you want to try them.
- No `.env` committed. Secrets never leave the judge's machine.
- No framework (LangChain, LangGraph, CrewAI). Every `if` / `await` is in
  `app/agent.py` and grep-able in one file.
