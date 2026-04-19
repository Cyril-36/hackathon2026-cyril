# Failure Modes

Six concrete failure classes the agent handles, each with the trigger, how
it's detected, the response, and the observable outcome in the audit log.
The taxonomy lives in `app/registry.py:_classify_error` and is applied
uniformly to every tool call and LLM call.

| Tag | Source | Retryable? | Terminal basis on exhaustion |
|---|---|---|---|
| `timeout` | `InjectedTimeout` or `asyncio.TimeoutError` | yes | `tool_failure` |
| `malformed_json` | `InjectedMalformed` | yes | `tool_failure` |
| `partial_fields` | `InjectedPartial` | yes | `tool_failure` |
| `schema_violation` | `pydantic.ValidationError` on a `response_schema` | yes | `tool_failure` |
| `stale_data` | `InjectedStale` | **no** | `tool_failure` |
| `rate_limit` / `schema_violation` on LLM path | `_RateLimit` / `ValidationError` / `JSONDecodeError` | yes (429), once (schema) | `low_confidence` |

---

## 1. Timeout on a read tool (`get_order`, `get_customer`, `search_knowledge_base`)

**Trigger.** `failures.should_fail()` chooses `"timeout"` for the ticket+tool.
`apply_failure()` sleeps past `CONFIG.tool_timeout_seconds` and raises
`InjectedTimeout`. The registry's `asyncio.wait_for(..., timeout=...)` will
also convert a real hang into `asyncio.TimeoutError`.

**Detection.** `registry.call_tool` catches the exception, `_classify_error`
tags it `"timeout"`, `_is_retryable` returns `True`.

**Response.** Exponential backoff with jitter
(`retry_base_delay * 2^attempt + random()*0.1`), up to `CONFIG.max_retries`
(default 2). Because `should_fail` only fires on `attempt == 0`, the retry
gets a clean attempt.

**Audit log evidence.**
```json
{
  "tools_used": ["get_customer", "get_order", "send_reply"],
  "failures": [{"tool": "get_order", "error": "timeout", "retry_count": 1, "recovered": true}],
  "recovery_attempted": true,
  "decision_basis": "recovered_and_resolved"
}
```

---

## 2. Malformed JSON from `get_product`

**Trigger.** `InjectedMalformed` is raised with payload
`'{"order_id": "ORD-1001", "amount": 12'` — a truncated JSON string.
(The exception itself carries the payload; no string ever crosses the
boundary as a "raw" dict, because Python callers don't parse tool returns —
the tool returns an actual `dict`. The malformed payload is the *concept*
the failure mode represents for the LLM channel; for tools, it's just a
flag that the schema contract is broken.)

**Detection.** `_classify_error` tags it `"malformed_json"`; retryable.

**Response.** Retry with backoff. Exhaustion writes to
`dead_letter_queue.json` and raises `RegistryError`.

**Audit log evidence.**
```json
{
  "failures": [{"tool": "get_product", "error": "malformed_json", "recovered": false}],
  "outcome": "escalated",
  "decision_basis": "tool_failure"
}
```

---

## 3. Partial response from `get_customer`

**Trigger.** `InjectedPartial` drops `tier` and `email` from the otherwise
valid customer payload.

**Detection.** `_classify_error` tags it `"partial_fields"` (it's our own
injected class — not a schema error raised by Pydantic). Retryable.

**Note on the related path.** When a tool returns a *dict* that a caller
has opted into validating via `response_schema=Customer`, Pydantic raises
`ValidationError` and the registry tags that `schema_violation` instead —
also retryable. The two tags share the same retry rule but tell the reader
*who* detected the problem.

**Audit log evidence.** On exhaustion:
```
[act] get_customer failed: partial_fields
[verify] blocked: critical tool unrecovered
[escalate] priority=medium — ...
```

---

## 4. Stale data on `get_order`

**Trigger.** `InjectedStale` is raised on `get_order` instead of returning
the payload. The failure is conceptually "status and delivery_date
contradict each other" — the classic stale-cache shape.

**Detection.** `_classify_error` tags it `"stale_data"`.
`_is_retryable("stale_data")` → **`False`**. A retry would fetch the same
stale cache and look just as "valid" — silently masking the inconsistency
is exactly what we don't want.

**Response.** The exception is terminal at the registry layer on its first
occurrence: `call_tool` records `Failure(tool="get_order", error="stale_data",
recovered=False)`, appends to `dead_letter_queue.json`, and raises
`RegistryError`. The agent catches that, `_verify` blocks any irreversible
write, and the ticket escalates.

Because `get_order` is in the critical-tool set, the resulting
`decision_basis` is **`tool_failure`** (an unrecovered failure on a
critical tool always wins in `compute_decision_basis`). This is the exact
outcome you can observe on **TKT-007** in any archived chaos run:

```
[act] get_order failed: stale_data
[verify] blocked: critical tool unrecovered
[escalate] priority=medium — category=...
[decision_basis] tool_failure
```

```bash
python -c "
import json, glob, os
latest = sorted(glob.glob('runs/*.json'), key=os.path.getmtime)[-1]
a = [x for x in json.load(open(latest)) if x['ticket_id']=='TKT-007'][0]
print('basis :', a['decision_basis'])
print('failures:', [(f['tool'], f['error'], f['recovered']) for f in a['failures']])
"
```

---

## 5. `check_refund_eligibility` throws

**Trigger.** The failure menu for this tool is `["throw"]`. `apply_failure`
raises `RuntimeError("eligibility service unavailable")`. This is a stand-in
for a real downstream outage — not one of the injected wrapper classes.

**Detection.** `_classify_error` falls through to
`type(exc).__name__` → `"RuntimeError"`. `_is_retryable` returns `False`
(we don't blindly retry unknown exception types), so this is terminal.

**Response.** `RegistryError` bubbles up. The registry also appends an
unrecovered `Failure(tool="check_refund_eligibility", recovered=False)` to
`state.failures`. `refund_guard` then returns
`(False, "eligibility result missing")`. `_verify` blocks the refund; the
ticket escalates. Because `any_unrecovered()` fires first inside
`compute_decision_basis`, the resulting `decision_basis` is **`tool_failure`**
— not `policy_guard`. (The `policy_guard` reason is still present on
`state.cache["guard_blocked"]` and surfaced in the escalation summary, but
the tool-failure signal wins as the machine-readable basis.)

**Audit log evidence.**
```
[act] eligibility failed: RuntimeError
[verify] refund blocked: eligibility result missing
[escalate] priority=medium
[decision_basis] tool_failure
```

---

## 6. LLM returns bad JSON or hits 429 (hybrid / llm mode)

**Trigger.** Groq occasionally returns prose-wrapped JSON or hits the
rate limit.

**Detection.** `registry.call_llm_structured`:
- `_extract_json(raw)` strips ``` fences and prose; if `json.loads` still
  fails → `ValidationError` / `JSONDecodeError` → tagged
  `"schema_violation"`.
- 429 is raised as `_RateLimit` by the transport.

**Response.**
- **One repair attempt** on schema violation: the original prompt is
  resent with the validation error appended and the previous raw output
  quoted, instructing the model to "Return ONLY valid JSON matching the
  schema."
- **Rate limit**: jittered exponential backoff up to
  `CONFIG.llm_max_rate_limit_retries` attempts.

On exhaustion the caller raises `RegistryError("llm", ...)`; `classify_ticket`
falls back to the deterministic rules classifier (with a confidence penalty);
`draft_reply` falls back to the rules template.

**Audit log evidence.**
```
[classify] llm: refund_request/medium conf=0.86 — ...
[llm_repair_attempted] fix: Field required [type=missing, input_value=...]
[llm_rate_limit_retry] 429 backoff 1.12s
[classify_fallback] llm failed: schema_violation; using rules
```

---

## Why each failure has a distinct outcome

| Failure | Retryable? | Final outcome |
|---|---|---|
| Timeout | yes | `recovered_and_resolved` on success; `tool_failure` on exhaustion |
| Malformed JSON | yes | Same pattern as timeout |
| Partial fields | yes | Same pattern as timeout |
| Schema violation (tool) | yes | Same pattern as timeout |
| Stale data | **no** | Terminal on first hit — `tool_failure` (critical tools always win in `compute_decision_basis`) |
| Eligibility throw | **no** | `tool_failure` — the unrecovered failure dominates, even though `refund_guard` also blocked |
| LLM schema / 429 | yes (both) | Rules-mode fallback with confidence penalty |

The registry distinguishes *transient* errors from *logic* errors, which
is what prevents retries from masking a genuine data inconsistency.
