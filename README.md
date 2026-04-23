# ShopWave Support Resolution System

> **A concurrent, tool-integrated support workflow that resolves routine
> ShopWave tickets, escalates risky cases with structured summaries, and
> produces a complete audit trail for every decision.**

Built for the Agentic AI Hackathon 2026.

Hosted dashboard: [Cyril-36/shopwave-dashboard](https://huggingface.co/spaces/Cyril-36/shopwave-dashboard)

---

## One-command run

```bash
./scripts/demo.sh
```

That script creates the venv, installs dependencies (first run only), and
executes all 20 tickets in deterministic `rules` mode with chaos=0. Output
is written to `audit_log.json`.

See [DEMO.md](DEMO.md) for the 3-minute live-demo script.

Low-level invocations (no bootstrap):

```bash
python run.py --mode rules                    # fully offline, deterministic
python run.py --mode rules --chaos 0.15 --archive  # chaos run → runs/<id>.json
cp .env.example .env                          # add GROQ_API_KEY=...
python run.py --mode hybrid                   # model-assisted classify + reply, rules pick tool chains
python run.py --mode llm                      # model-driven end-to-end
```

Reproducible container image (zero host dependencies):

```bash
docker build -t shopwave-agent .
docker run --rm shopwave-agent                 # clean run, 20 tickets, chaos=0
docker run --rm shopwave-agent python run.py --mode rules --chaos 0.15 --seed 42
docker run --rm -p 8787:8787 shopwave-agent web # launch the realtime web UI
```

### Web UI (optional)

A self-contained React operations console lives under `frontend/`. It talks to the same
runtime via a FastAPI server (`app/server.py`) and streams the live
classify / tool / decide / recover / ticket_done events over Server-Sent
Events — you watch the execution trace build live as each ticket runs.

```bash
./scripts/demo_web.sh                           # → http://127.0.0.1:8787/ui/
```

The shipped console loads only local files from `/ui/`: `fonts.css`,
`styles.css`, `bundle.js`, and vendored font assets. If you edit the
frontend source files, regenerate those assets with:

```bash
npm install
npm run build:frontend
```

On first load it calls `/api/snapshot` and renders the last completed run
(the one stored in `audit_log.json`), so the console is fully navigable
even without re-running the agent. Click **▶ Run Agent** (or press ⌘R) to
kick off a fresh run — the ticket list repopulates, each ticket's Trace
tab streams in real time, and the Decision Basis bar updates as outcomes
land. Toggle **Chaos** in the Tweaks panel (press `?`) to see the retry
and recovery paths light up.

The CLI, verifier, tests, and default Docker CMD do **not** import the
server. `app/events.py` is only loaded when the server starts, so
instrumenting the web path added zero runtime cost to the submission path.

### CLI flags

| Flag | Meaning |
|---|---|
| `--mode {rules,llm,hybrid}` | Execution mode. Default from `MODE` env var (`hybrid`). |
| `--chaos 0.0-1.0` | Failure injection rate. Default from `CHAOS` (0.08). |
| `--seed N` | Deterministic chaos. Default 42. |
| `--ticket TKT-003` | Run a single ticket. |
| `--today YYYY-MM-DD` | Pin global "now" for return-window math; per-ticket `created_at` still wins where set. |
| `--audit-out PATH` | Write the audit log to a specific path instead of `audit_log.json`. |
| `--archive` | Divert the audit log to `runs/<run_id>.json` (chaos experiments won't overwrite the clean submission log). |

CLI flags are applied to `os.environ` **before** any `app.*` module is imported,
so they actually override the frozen `Config` (which reads env at module-import
time). That's the reason every `import` inside `run.py` is lazy — see
`run.py:50-58`.

---

## What makes this more than a script

- **Custom orchestrator**, not a framework graph. Every edge is a Python
  `if` / `await` we can point to in `app/agent.py`.
- **Seven-step loop**: `CLASSIFY → PLAN → ACT → VERIFY → EVALUATE → RESOLVE/ESCALATE → LOG`.
  `VERIFY` answers *"is it safe to act?"*; `EVALUATE` answers *"do we trust our
  own work?"* The split maps directly to the rubric's autonomy and
  evaluation categories.
- **Dual confidence thresholds**: `0.70` for reversible actions
  (`send_reply`), `0.85` for irreversible ones (`issue_refund`). A wrong
  reply is embarrassing; a wrong refund costs money.
- **DOA still follows the refund guardrail order.**
  Damaged-on-arrival tickets (`TKT-008`) still call
  `check_refund_eligibility` before `issue_refund`; KB §1.5 changes the
  eligibility outcome ("eligible regardless of return window"), not the
  requirement to verify eligibility before an irreversible action.
- **Per-ticket effective date**: return-window and warranty math use each
  ticket's `created_at`, not a global "today". The dataset deliberately has
  tickets created weeks apart; a single `CONFIG.today` would silently pass
  stale-window cases.
- **Eleven tools** (the brief's 8 core + 3 extensions: `get_customer_orders`,
  `cancel_order`, `initiate_exchange`) driven through a unified `registry.py` that provides
  exponential-backoff retry, Pydantic schema validation at the response
  boundary, DLQ on exhaustion, and the same 429-handling + single-shot repair
  flow for model calls.
- **Pure decision functions in `policies.py`** — tier-aware refund guard,
  warranty-vs-refund routing, social-engineering detection, registered-online
  decline, order-conflict detection, return-window math — all unit-testable
  without running the agent.
- **Seven decision-basis tags** emitted per ticket, giving reviewers a one-line
  `grep` into the agent's distribution of outcomes:

| Tag | Meaning |
|---|---|
| `successful_resolution` | Happy path, no recovery needed |
| `recovered_and_resolved` | Tool failed, retry succeeded, ticket resolved |
| `policy_guard` | Safety rule blocked the write (expired window, registered online, confidence floor, etc.) |
| `low_confidence` | Classification confidence below threshold, escalated |
| `tool_failure` | Unrecovered failure routed the ticket to a human |
| `unresolvable_ticket` | Agent couldn't even begin (spam, no identity, etc.) |
| `fraud_detected` | Social-engineering pattern matched, escalated urgently |

---

## Architecture

See `architecture.png` (rendered from `scripts/gen_architecture.py`) for the
one-page view, and `demo.mp4` for the recorded walkthrough. The textual summary:

```
 run.py ──▶  asyncio.gather + Semaphore(5)  ──▶  process_ticket × 20 (parallel)
                             │
                             ▼
          ┌──────────────────────────────────────────────┐
          │         agent.process_ticket                 │
          │                                              │
          │  1. CLASSIFY  (llm.py)                       │
          │  2. PLAN      (policies.chain_template)      │
          │  3. ACT       (tools.*, via registry)        │
          │  4. VERIFY    (refund_guard, non_returnable, │
          │                conflict, critical-failure)   │
          │  5. EVALUATE  (confidence adjust)            │
          │  6. RESOLVE / ESCALATE / DECLINE             │
          │  7. LOG       (AuditEntry → audit_log.json)  │
          └──────────────────────────────────────────────┘

 registry.py (unified plumbing) wraps every tool + every model call
 ─────────────────────────────────────────────────────────────────
   • timeout             → exponential backoff, up to max_retries
   • malformed JSON      → Pydantic validation; model gets one repair pass
   • partial fields      → Pydantic catches, registry retries
   • stale data          → caller sees schema-valid; NOT retryable (KB rule)
   • 429 rate limits     → jittered backoff on model path
   • retries exhausted   → write to dead_letter_queue.json, raise RegistryError
```

Regenerate the diagram with:

```bash
python scripts/gen_architecture.py
```

---

## File guide

| Path | Role |
|---|---|
| `run.py` | CLI entry, concurrency runner, summary printer |
| `app/config.py` | Single source of thresholds, paths, modes |
| `app/state.py` | `TicketState` dataclass — the thread through the loop |
| `app/models.py` | Pydantic schemas for tickets, tools, model I/O, audit |
| `app/tools.py` | 11 async tools (8 brief-mandated + 3 extensions) + in-memory idempotency lock for `issue_refund` |
| `app/failures.py` | Realistic failure types, seeded for reproducibility |
| `app/registry.py` | Unified retry / Pydantic validation / DLQ / response repair |
| `app/llm.py` | Model adapters + deterministic rules fallback |
| `app/policies.py` | Pure decision functions (no I/O, fully testable) |
| `app/agent.py` | Orchestrator: the 7-step loop |
| `scripts/gen_architecture.py` | Renders `architecture.png` with Pillow |
| `tests/test_recovery.py` | 6 focused unit + smoke tests |
| `tests/test_dataset.py` | 10 end-to-end regressions pinning per-ticket behaviour |
| `tests/test_chaos_seed.py` | 7 tests pinning chaos-injection reproducibility |
| `tests/test_cli.py` | 2 subprocess integration tests for `--archive` and fresh-interpreter chaos reproducibility |
| `data/` | ShopWave fixture dataset (tickets, customers, orders, products, KB) |
| `audit_log.json` | Clean submission log (one entry per ticket) |
| `runs/` | Chaos-experiment archives (git-ignored) |
| `dead_letter_queue.json` | Generated on exhausted retries |
| `architecture.png` | One-page architecture diagram |
| `DEMO.md` | 3-minute live-demo script |
| `demo.mp4` | Recorded judge walkthrough covering clean run, DOA compliance, and chaos recovery |
| `scripts/demo.sh` | One-command bootstrap + run |
| `scripts/gen_architecture.py` | Regenerates `architecture.png` |

---

## The eleven tools (8 core + 3 extensions)

| Tool | Read/Write | Failure modes injected |
|---|---|---|
| `get_order(order_id)` | read | timeout, stale data |
| `get_customer(email)` | read | timeout, partial fields |
| `get_customer_orders(email)` | read | — (used when no order id supplied) |
| `get_product(product_id)` | read | malformed JSON |
| `search_knowledge_base(q)` | read | timeout, empty result |
| `check_refund_eligibility(order_id, today, category)` | read-decision | **may throw** (per spec) |
| `issue_refund(order_id, amount)` | **write, irreversible** | per-order asyncio.Lock guards double-refund |
| `send_reply(ticket_id, message)` | write | occasional timeout |
| `escalate(ticket_id, summary, priority)` | write | never fails (terminal) |
| `cancel_order(order_id)` | write | policy-checked (processing status only) |
| `initiate_exchange(order_id, variant)` | write | occasional timeout |

`get_customer`, `get_order` and `get_product` are called with a `response_schema`
kwarg (Pydantic `Customer` / `Order` / `Product`). The registry validates the
tool's output against that schema and converts partial/malformed payloads into
`partial_fields` / `malformed_json` failures the retry loop can act on.

---

## Dataset-aware behaviour

A few tickets in the fixture set exist specifically to probe edge cases. The
regression tests in `tests/test_dataset.py` lock in the correct response:

| Ticket | Scenario | Correct behaviour |
|---|---|---|
| `TKT-002` | Delivered 2024-03-04, deadline 2024-03-19, ticket created 2024-03-22 | Window **has expired** from the ticket's perspective; do **not** issue refund. Rerouted to warranty team (warranty still active). |
| `TKT-005` | VIP with "management pre-approval" in customer notes, window expired | Approve the return under the KB §2.3 pre-approved exception. |
| `TKT-006` | "I just placed an order…" but `get_customer_orders` resolves to ORD-1006 which is already delivered | Detect the conflict, do not cancel, escalate with a specific reason. |
| `TKT-008` | Damaged on arrival | Refund under KB §1.5, but only after `check_refund_eligibility` confirms the DOA-specific exception. |
| `TKT-010` | Shipping inquiry | Surface TRK-88291 verbatim in the reply. |
| `TKT-013` | Return window expired **and** device registered online | First-class `declined` outcome (not a generic escalation) citing both reasons. |
| `TKT-018` | Standard-tier customer claims premium "instant refund" policy | Fraud-detected escalation, urgent priority. |
| `TKT-020` | Completely ambiguous body, no ID | Resolve by asking 3 targeted clarifying questions rather than escalating on low confidence. |

---

## Tests

```bash
pytest -q
```

The current suite contains **98 tests across 10 files**. It covers:

- orchestration, recovery, retry classification, and irreversible-action guardrails
- dataset regressions for the high-value edge cases in `data/tickets.json`
- seeded chaos reproducibility, archived-run distribution, and stale-data terminal paths
- registry validation, model-response repair, and token-accounting correctness
- CLI subprocess flows, mode-comparison checks, server request validation, and frontend layout smoke coverage

The suite is local-only; the default `rules` mode tests make no network calls.

---

## Security

- `.env` is gitignored; only `.env.example` is committed.
- `.gitignore` also excludes `dead_letter_queue.json`, caches, and OS cruft.
- No API keys required for the default `rules` mode demo.

---

## Reading the audit log

```bash
python -c "import json; d=json.load(open('audit_log.json'));
from collections import Counter;
print(Counter(e['decision_basis'] for e in d))"
```

With `--mode rules --chaos 0` the distribution is:

```
Counter({'successful_resolution': 14, 'policy_guard': 3, 'low_confidence': 2, 'fraud_detected': 1})
```

Every ticket's `reasoning_trace` field is one compact line per step — a whole
ticket's decision-path reads in under five seconds.
