"""FastAPI server — the web UI's backend for realtime agent runs.

Routes:
  GET  /                        redirect to /ui/
  GET  /ui/...                  static mount over ./frontend/
  GET  /api/snapshot            last audit_log.json + fixtures, adapted
  GET  /api/tickets             adapted fixture list (pre-run)
  POST /api/run                 spawn a run, returns {run_id}
  GET  /api/events?run_id=...   SSE stream of events; closes on run_done
  GET  /api/dlq                 dead_letter_queue.json if present
  GET  /api/health              liveness probe

This file is only imported when the user starts `uvicorn app.server:app`.
The CLI (`run.py`) never touches it, so all `app.events` imports are
isolated to the web path. See SUBMISSION.md for scope.

Concurrency model:
  - One uvicorn process, one EventBus (app/events.BUS)
  - Each /api/run spawns a background asyncio.Task
  - Inside the task we iterate tickets under the same Semaphore the CLI uses
  - Each ticket gets its own Emitter bound to run_id + ticket_id so events
    from concurrent tickets don't blur

Safety notes:
  - CONFIG is a frozen dataclass, so /api/run overrides mode/chaos/seed
    via object.__setattr__ on the shared instance. This is acceptable
    for a single-user demo; it would not be acceptable in production.
  - /api/run is serialised via an asyncio.Lock to prevent two concurrent
    runs from clobbering each other's CONFIG overrides.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import CONFIG
from app.events import BUS, Emitter, Event
from app.frontend_data import (
    adapt_audit_to_frontend,
    adapt_event,
    adapt_ticket_start,
    load_fixtures,
    load_snapshot,
)

# Lazy imports so test suites don't break if fastapi not installed at that point
_agent_mod = None
_models_mod = None


def _lazy_imports():
    global _agent_mod, _models_mod
    if _agent_mod is None:
        from app import agent as _agent_mod  # noqa: F811
        from app import models as _models_mod  # noqa: F811
    return _agent_mod, _models_mod


ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
RUN_LOCK = asyncio.Lock()


app = FastAPI(title="ShopWave Support Console", version="1.0")


# Serve the React/CDN frontend
if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Liveness + mode-availability preflight for the UI.

    The frontend disables the `hybrid`/`llm` toggles when the configured
    provider can't actually run (e.g. no GROQ_API_KEY). `rules` is always
    available because it's fully offline.
    """
    provider = CONFIG.llm_provider
    if provider == "groq":
        llm_ok = bool(CONFIG.groq_api_key)
        reason = None if llm_ok else "GROQ_API_KEY not set"
    elif provider == "ollama":
        # Can't cheaply confirm ollama without an HTTP probe; be optimistic.
        llm_ok = True
        reason = None
    else:
        llm_ok = False
        reason = f"unknown provider: {provider}"
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "llm_provider": provider,
        "modes": {"rules": True, "hybrid": llm_ok, "llm": llm_ok},
        "llm_unavailable_reason": reason,
    }


@app.get("/api/snapshot")
async def snapshot(run_id: Optional[str] = None) -> JSONResponse:
    """Return an adapted audit artifact.

    Query param `run_id`:
      • (unset)   → `audit_log.json` (the clean submission artifact)
      • `latest`  → most recently archived web run under `runs/`
      • `<id>`    → a specific `runs/<id>.json`
    Falls back to the clean audit log if a requested archive is missing.
    """
    try:
        payload = load_snapshot(run_id=run_id)
        return JSONResponse(payload)
    except Exception as exc:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail=f"snapshot_error: {exc}")


@app.get("/api/tickets")
async def tickets() -> JSONResponse:
    """Adapted fixtures, no audit overlay — useful pre-run."""
    fixtures = load_fixtures()
    rows = [adapt_ticket_start(tid, fx) for tid, fx in fixtures.items()]
    return JSONResponse({"tickets": rows, "tools": _tool_meta()})


@app.get("/api/dlq")
async def dlq() -> JSONResponse:
    path = Path(CONFIG.dlq_path)
    if not path.exists():
        return JSONResponse([])
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse([])


class RunRequest(BaseModel):
    mode: Literal["rules", "llm", "hybrid"] = Field(default_factory=lambda: CONFIG.mode)
    chaos: float = Field(default_factory=lambda: CONFIG.chaos_rate, ge=0.0, le=1.0)
    seed: Optional[int] = None
    tickets: Optional[list[str]] = None


@app.post("/api/run")
async def start_run(req: RunRequest) -> JSONResponse:
    """Spawn a background run and return its run_id.

    Subscribers should connect to /api/events?run_id=... to stream live.
    """
    _agent, _models = _lazy_imports()

    run_id = _agent.new_run_id()
    fixtures = load_fixtures()
    all_tickets = list(fixtures.values())
    if req.tickets:
        wanted = set(req.tickets)
        all_tickets = [t for t in all_tickets if t.ticket_id in wanted]
    if not all_tickets:
        raise HTTPException(status_code=400, detail="no tickets matched")

    asyncio.create_task(_run_async(req, run_id, all_tickets))
    return JSONResponse({"run_id": run_id, "ticket_count": len(all_tickets)})


async def _run_async(req: RunRequest, run_id: str, tickets_list: list) -> None:
    """The actual orchestration. Runs under a lock to avoid CONFIG thrashing."""
    _agent, _models = _lazy_imports()

    async with RUN_LOCK:
        # Apply mode/chaos/seed overrides onto the frozen CONFIG.
        # Mutating object.__setattr__ is the escape hatch; only used here.
        overrides = {
            "mode": req.mode,
            "chaos_rate": max(0.0, min(1.0, float(req.chaos))),
        }
        if req.seed is not None:
            overrides["seed"] = int(req.seed)
        _push_config_overrides(overrides)

        # Clear DLQ for this run
        dlq_path = Path(CONFIG.dlq_path)
        if dlq_path.exists():
            try:
                dlq_path.unlink()
            except OSError:
                pass

        start_ts = time.monotonic()
        emitter = Emitter(BUS, run_id, start_ts)
        emitter.emit(
            "run_start",
            mode=CONFIG.mode,
            chaos=CONFIG.chaos_rate,
            seed=CONFIG.seed,
            ticket_count=len(tickets_list),
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        sem = asyncio.Semaphore(CONFIG.max_concurrent_tickets)

        async def _run_one(t):
            async with sem:
                ticket_emitter = emitter.bind_ticket(t.ticket_id)
                try:
                    return await _agent.process_ticket(
                        t, run_id=run_id, emitter=ticket_emitter
                    )
                except Exception as exc:  # pragma: no cover — defensive
                    ticket_emitter.emit("ticket_error", error=str(exc)[:240])
                    return None

        try:
            results = await asyncio.gather(*(_run_one(t) for t in tickets_list))
        except Exception as exc:  # pragma: no cover
            emitter.emit("run_error", error=str(exc)[:240])
            BUS.close_run(run_id)
            return

        # Persist the run to runs/<run_id>.json so the clean audit_log.json
        # stays untouched while the UI is driving experiments.
        results_ok = [r for r in results if r is not None]
        archive_dir = ROOT / "runs"
        archive_dir.mkdir(exist_ok=True)
        archive_path = archive_dir / f"{run_id}.json"
        archive_path.write_text(
            json.dumps([r.model_dump(mode="json") for r in results_ok], indent=2),
            encoding="utf-8",
        )

        emitter.emit(
            "run_done",
            ticket_count=len(results_ok),
            archive_path=f"runs/{run_id}.json",
            ended_at=datetime.now(timezone.utc).isoformat(),
        )


@app.get("/api/events")
async def events(run_id: str, request: Request) -> StreamingResponse:
    """SSE stream for a run. Closes when run_done fires (or client disconnects)."""

    async def event_stream():
        # Send an initial comment so intermediary proxies flush headers
        yield ": connected\n\n"
        async for ev in BUS.subscribe(run_id):
            if await request.is_disconnected():
                break
            wire = adapt_event(ev)
            data = json.dumps(wire, default=str)
            yield f"data: {data}\n\n"
            if ev.type == "run_done":
                yield "event: done\ndata: {}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx passthrough
            "Connection": "keep-alive",
        },
    )


# ---- Helpers --------------------------------------------------------------


def _push_config_overrides(overrides: dict[str, Any]) -> None:
    """Apply overrides to the frozen CONFIG instance. Single-user demo scope."""
    for k, v in overrides.items():
        if hasattr(CONFIG, k):
            try:
                object.__setattr__(CONFIG, k, v)
            except Exception:  # pragma: no cover
                pass


def _tool_meta() -> list[dict[str, Any]]:
    from app.frontend_data import TOOL_META

    return TOOL_META


# Silence an unused reference so linters stay quiet
_ = Event
