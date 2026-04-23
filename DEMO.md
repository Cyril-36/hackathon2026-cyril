# 3-Minute Live Demo Script

> One-command setup, three diagnostic tickets, one chaos rerun. Designed to
> be safe to run live in front of judges with no network dependencies.

## Pre-flight (do this once, before the timer starts)

```bash
./scripts/demo.sh          # creates venv, installs deps, runs 20 tickets
```

You should see the clean-run distribution:

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
audit_log -> audit_log.json
```

Note: `recovery_attempted: 0` is **correct** for `--chaos 0`. Act 4 below
runs the chaos path and will show real recovery numbers. The clean log on
its own doesn't prove recovery — only the archived chaos run does.

Keep the terminal open.

---

## Act 1 — the happy-path resolution loop (45s)

**Talking point:** "Every ticket runs through seven steps. Let's look at a
refund case — the system chains six tool calls to resolve it end-to-end."

```bash
python scripts/show_ticket.py TKT-001
```

Call out:
- six tools used in a single chain (get_customer → get_order → get_product →
  check_refund_eligibility → issue_refund → send_reply)
- `decision_basis = successful_resolution`
- the `reasoning_trace` field is one line per step, not a blob

---

## Act 2 — safe escalation (45s)

**Talking point:** "A system that always says yes is useless. TKT-006
claims 'just placed, please cancel' — but the order is already delivered.
The system detects the contradiction and refuses to act."

```bash
python scripts/show_ticket.py TKT-006
```

Call out:
- email lookup resolved the order, *then* conflict detection blocked it
- escalation_summary carries the specific reason a human reviewer needs

---

## Act 3 — fraud detection (30s)

**Talking point:** "TKT-018 claims a 'premium instant refund policy' that
doesn't exist. Customer record says standard tier. This is social
engineering — we don't just refuse, we flag it as fraud."

```bash
python scripts/show_ticket.py TKT-018 --no-trace
```

Call out:
- `fraud_detected` is a first-class decision_basis, not a fallback
- priority bumps to `urgent`
- no money moved, no data leaked

---

## Act 4 — recovery under chaos (45s)

**Talking point:** "Production tools fail. Under 15% chaos the system
attempts recovery on six tickets, resolves one after retries, and still
refuses to retry stale data — exactly the right behaviour."

```bash
./scripts/demo.sh --chaos 0.15 --seed 42
```

Expect (deterministic with the stable SHA-256 seed):

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
audit_log -> runs/run_...Z_....json
```

**Strongest recovery evidence — TKT-011.** Two *separate* transient failures
(`get_product:malformed_json` and `initiate_exchange:timeout`) both recover
and the ticket still resolves cleanly.

```bash
python scripts/show_latest_ticket.py TKT-011 --no-trace
```

Call out:
- chaos reruns **divert** to `runs/` — the clean `audit_log.json` is never
  silently overwritten by an experiment
- `recovered_and_resolved` vs `tool_failure` — the registry distinguishes
  transient errors from real logic errors (`stale_data` is **never** retried;
  you can see that on TKT-007 in the archived run)

---

## Act 5 — realtime web console (optional, 30s)

If a judge asks what a live operator's experience looks like:

```bash
./scripts/demo_web.sh                  # → http://127.0.0.1:8787/ui/
```

On page load, `/api/snapshot` hydrates the console from the last
`audit_log.json` — the list, the per-ticket trace, Recovery tab and Raw
JSON are all navigable without clicking Run. Then click **▶ Run Agent**
(or press ⌘R) with Chaos on from the Tweaks panel (`?`) and watch the
same execution loop we just ran on the CLI stream in live:

- Ticket rows flip from `pending` → `running` → `resolved / escalated /
  declined` as `ticket_done` events arrive
- The trace pane on the selected ticket gets rows pushed in as the system
  calls `get_order`, `check_refund_eligibility`, `issue_refund`, etc.,
  each with real `+Xms` latency and inline args/result preview
- On chaos, red `tool!` rows appear followed by amber `↻ recovered`
  rows — the same failure/recovery story from Act 4, visible instead of
  read from JSON
- Decision Basis bar in the dashboard reallocates across
  `successful_resolution / recovered_and_resolved / policy_guard /
  tool_failure / low_confidence / fraud_detected` as the run finishes

All decision behaviour is identical to the CLI path (`run.py` never imports
the server). The web layer only wraps the same `process_ticket` loop
with an event emitter so the browser can render the trace live.

---

## If asked "does the agent ever bypass the refund guard?"

No. Even damaged-on-arrival cases still check refund eligibility before a
refund is issued. The policy affects the eligibility decision, not the order
of operations. If you need to show it, point to `app/agent.py` (`_act`),
`app/tools.py` (`check_refund_eligibility`), and `TKT-008` in the clean log.

```bash
python scripts/show_ticket.py TKT-008 --no-trace
```

You should see a `successful_resolution` where
`check_refund_eligibility` appears before `issue_refund`.

---

## If asked "can you run one specific ticket end-to-end?"

```bash
python run.py --mode rules --ticket TKT-013 --today 2024-04-01
python scripts/show_ticket.py TKT-013 --no-trace
```

This should return `declined` with both reasons in the reply: the order was
registered online, and the return window had expired.

---

## What not to show

- A chaos rerun with a different seed each time. The run should stay
  reproducible, so use `--seed 42`.
- The `hybrid` or `llm` modes without an API key in `.env`, because that
  fallback path is not useful to explain in a short demo.
