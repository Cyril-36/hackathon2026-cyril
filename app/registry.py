"""Unified retry/validation layer. Wraps tool calls AND LLM calls.

Design rule: every value that crosses this boundary has been Pydantic-validated.
If validation fails after repair attempts, the caller gets a structured
RegistryError — never a silent None, never a half-parsed dict.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.config import CONFIG
from app.failures import (
    InjectedFailure,
    InjectedMalformed,
    InjectedPartial,
    InjectedStale,
    InjectedTimeout,
)
from app.models import Failure
from app.state import TicketState
from app.tools import TOOL_REGISTRY


class RegistryError(Exception):
    """Raised when a tool call cannot produce a valid result after retries."""

    def __init__(self, tool: str, reason: str, attempts: int):
        super().__init__(f"{tool} failed after {attempts} attempts: {reason}")
        self.tool = tool
        self.reason = reason
        self.attempts = attempts


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, InjectedTimeout) or isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, InjectedMalformed):
        return "malformed_json"
    if isinstance(exc, InjectedPartial):
        return "partial_fields"
    if isinstance(exc, InjectedStale):
        return "stale_data"
    if isinstance(exc, ValidationError):
        return "schema_violation"
    return type(exc).__name__


def _is_retryable(tag: str) -> bool:
    # Transient failures retry; logic errors (stale data) don't.
    return tag in {"timeout", "malformed_json", "partial_fields", "schema_violation"}


def _append_dlq(entry: dict[str, Any]) -> None:
    path = Path(CONFIG.dlq_path)
    existing: list[Any] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _drain_llm_usage(state: TicketState) -> None:
    """Accumulate provider-published usage for the current LLM attempt.

    The transport exposes usage via app.llm._LAST_USAGE. Drain it immediately
    after each response so billed malformed/repair attempts are still counted
    and later attempts cannot overwrite earlier usage.
    """
    try:
        from app.llm import _LAST_USAGE

        usage = _LAST_USAGE.get()
        if usage:
            state.tokens_in += int(usage.get("prompt_tokens") or 0)
            state.tokens_out += int(usage.get("completion_tokens") or 0)
            _LAST_USAGE.set({})
    except Exception:
        pass


async def call_tool(
    tool_name: str,
    state: TicketState,
    *,
    response_schema: type[BaseModel] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a tool through retry + validation + DLQ."""
    fn: Callable[..., Awaitable[dict[str, Any]]] = TOOL_REGISTRY[tool_name]
    attempts = 0
    last_error = "unknown"
    started = time.monotonic()

    # Truncated kwargs preview for the UI — mirrors DLQ formatting so we
    # don't leak full objects into the event stream.
    kwargs_preview = {k: str(v)[:120] for k, v in kwargs.items()}
    if state.emitter is not None:
        state.emitter.emit("tool_start", tool=tool_name, args=kwargs_preview)

    while attempts <= CONFIG.max_retries:
        ctx = {"ticket_id": state.ticket.ticket_id, "attempt": attempts}
        try:
            attempt_started = time.monotonic()
            result = await asyncio.wait_for(
                fn(ctx, **kwargs), timeout=CONFIG.tool_timeout_seconds
            )
            # Validate — schema is the contract.
            if response_schema is not None and result.get("found", True):
                response_schema.model_validate(result)
            state.tools_used.append(tool_name)
            ms = int((time.monotonic() - attempt_started) * 1000)
            if attempts > 0:
                state.recovery_attempted = True
                state.failures.append(
                    Failure(
                        tool=tool_name,
                        error=last_error,
                        retry_count=attempts,
                        recovered=True,
                    )
                )
                if state.emitter is not None:
                    state.emitter.emit(
                        "tool_recovered",
                        tool=tool_name,
                        attempts=attempts,
                        last_error=last_error,
                    )
            if state.emitter is not None:
                state.emitter.emit(
                    "tool_end",
                    tool=tool_name,
                    ms=ms,
                    attempts=attempts,
                    result=_preview_result(result),
                )
            return result
        except BaseException as exc:  # noqa: BLE001 — registry is the boundary
            tag = _classify_error(exc)
            last_error = tag
            attempts += 1
            if state.emitter is not None:
                state.emitter.emit(
                    "tool_failure",
                    tool=tool_name,
                    error=tag,
                    attempt=attempts,
                    retryable=_is_retryable(tag),
                )
            if attempts > CONFIG.max_retries or not _is_retryable(tag):
                state.failures.append(
                    Failure(
                        tool=tool_name,
                        error=tag,
                        retry_count=attempts - 1,
                        recovered=False,
                    )
                )
                _append_dlq(
                    {
                        "ticket_id": state.ticket.ticket_id,
                        "tool": tool_name,
                        "kwargs": {k: str(v)[:80] for k, v in kwargs.items()},
                        "error": tag,
                        "attempts": attempts,
                        "elapsed_s": round(time.monotonic() - started, 3),
                    }
                )
                if state.emitter is not None:
                    state.emitter.emit(
                        "tool_dead_lettered",
                        tool=tool_name,
                        error=tag,
                        attempts=attempts,
                    )
                raise RegistryError(tool_name, tag, attempts) from exc
            # exponential backoff with jitter
            delay = CONFIG.retry_base_delay * (2 ** (attempts - 1))
            delay += random.random() * 0.1
            await asyncio.sleep(delay)
    # unreachable
    raise RegistryError(tool_name, last_error, attempts)


def _preview_result(result: Any) -> Any:
    """Truncate tool results for wire transmission — keep top-level keys only,
    stringify anything nested, clip strings. Protects the event stream from
    blowing up when a tool returns a large payload."""
    if not isinstance(result, dict):
        return str(result)[:240]
    preview: dict[str, Any] = {}
    for k, v in list(result.items())[:12]:
        if isinstance(v, (str, int, float, bool)) or v is None:
            preview[k] = v if not isinstance(v, str) else v[:240]
        elif isinstance(v, list):
            preview[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            preview[k] = f"{{{len(v)} keys}}"
        else:
            preview[k] = str(v)[:120]
    return preview


# ---- LLM call wrapper -----------------------------------------------------


async def call_llm_structured(
    call: Callable[[str], Awaitable[str]],
    prompt: str,
    schema: type[BaseModel],
    state: TicketState,
) -> BaseModel:
    """Call the LLM, validate JSON output against schema, repair once if needed.

    Retries on 429 rate limits with jitter. One repair attempt on schema
    violations. After that, raises RegistryError so the agent can escalate.
    """
    attempts = 0
    rate_limit_retries = 0
    last_raw = ""
    last_error = "unknown"
    current_prompt = prompt

    while attempts <= CONFIG.llm_repair_attempts:
        try:
            raw = await call(current_prompt)
            last_raw = raw
            _drain_llm_usage(state)
            data = _extract_json(raw)
            result = schema.model_validate(data)
            state.llm_calls += 1  # count successful LLM calls only
            return result
        except _RateLimit as exc:
            rate_limit_retries += 1
            if rate_limit_retries > CONFIG.llm_max_rate_limit_retries:
                raise RegistryError("llm", "rate_limit_exhausted", rate_limit_retries) from exc
            delay = CONFIG.retry_base_delay * (2 ** rate_limit_retries) + random.random() * 0.3
            state.log("llm_rate_limit_retry", f"429 backoff {delay:.2f}s")
            await asyncio.sleep(delay)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = "schema_violation"
            attempts += 1
            if attempts > CONFIG.llm_repair_attempts:
                raise RegistryError("llm", last_error, attempts) from exc
            state.log("llm_repair_attempted", f"fix: {str(exc)[:120]}")
            # Keep the repair prompt minimal — re-sending the full prompt + raw
            # output makes things worse when the original failure was truncation.
            fields = ", ".join(schema.model_fields.keys())
            current_prompt = (
                f"Return ONLY valid JSON with exactly these fields: {fields}\n"
                f"Error in your previous response: {str(exc)[:120]}\n"
                f"JSON only — no prose, no markdown fences, no explanation."
            )

    raise RegistryError("llm", last_error, attempts)


class _RateLimit(Exception):
    pass


def raise_rate_limit() -> None:
    raise _RateLimit()


def _extract_json(raw: str) -> Any:
    """Tolerate LLMs that wrap JSON in ```json fences or prose.

    Uses JSONDecoder.raw_decode instead of first-{...last-} slicing so nested
    JSON objects and trailing content don't confuse the parser.
    """
    s = raw.strip()
    # Strip markdown code fences
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # Skip leading prose to find the first '{' that starts a JSON object
    if not s.startswith("{"):
        i = s.find("{")
        if i == -1:
            raise json.JSONDecodeError("no JSON object found", s, 0)
        s = s[i:]
    # raw_decode stops at the first complete JSON value — immune to trailing
    # content or multiple JSON objects in the string.
    obj, _ = json.JSONDecoder().raw_decode(s)
    return obj
