"""Realtime event bus for the optional web UI.

The CLI, tests, verifier, and default Docker CMD do NOT import this module —
it's only loaded by `app/server.py`. The agent gets an optional
`state.emitter` reference; when `emitter is None` (the CLI path) every emit
site is a no-op, so behaviour of `run.py` is unchanged.

Design notes:
- Per-run fan-out via `asyncio.Queue` per subscriber. One SSE request == one
  subscriber. A publish without subscribers is a no-op (events are not
  buffered server-side; subscribers that join late miss early frames — but
  they can call `/api/snapshot` to rehydrate).
- `Emitter` is a thin binding (run_id + ticket_id + start_ts) so callers
  don't have to thread timing math through every emit.
- Timing is `monotonic()` so we're immune to wall-clock skew.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional  # noqa: F401  — Optional retained for future extension


@dataclass
class Event:
    run_id: str
    ticket_id: str | None
    type: str
    ts_ms: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ticket_id": self.ticket_id,
            "type": self.type,
            "ts_ms": self.ts_ms,
            "payload": self.payload,
        }


class EventBus:
    """Process-local pub/sub with per-run replay buffer.

    Why the buffer: a rules-mode run over 20 tickets finishes in under a
    second. A browser subscriber that opens EventSource *after* the POST
    /api/run response would miss all the events. We keep a bounded history
    per run_id and replay it to new subscribers before they start tailing.
    """

    HISTORY_CAP = 2000  # plenty of headroom for a 20-ticket chaos run
    # retain finished run history for this many seconds so tab reloads work
    FINISHED_TTL_SECONDS = 120

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event | None]]] = {}
        self._history: dict[str, list[Event]] = {}
        self._finished_at: dict[str, float] = {}

    def publish(self, ev: Event) -> None:
        hist = self._history.setdefault(ev.run_id, [])
        hist.append(ev)
        if len(hist) > self.HISTORY_CAP:
            # drop oldest to keep memory bounded (ring buffer behaviour)
            del hist[: len(hist) - self.HISTORY_CAP]
        if ev.type == "run_done":
            self._finished_at[ev.run_id] = time.monotonic()
        self._gc()
        for q in list(self._subs.get(ev.run_id, ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    def _gc(self) -> None:
        """Drop history for runs that finished more than FINISHED_TTL ago
        AND have no active subscribers."""
        now = time.monotonic()
        for run_id, finished_ts in list(self._finished_at.items()):
            if now - finished_ts <= self.FINISHED_TTL_SECONDS:
                continue
            if self._subs.get(run_id):
                continue
            self._history.pop(run_id, None)
            self._finished_at.pop(run_id, None)

    async def subscribe(self, run_id: str) -> AsyncIterator[Event]:
        """Replay buffered history for run_id, then yield live events.

        Terminates when a run_done event is observed (either in history or
        from the live tail) or when the sentinel `None` is posted.

        Race handling: we register the queue FIRST (so new events start
        buffering), then snapshot how many history items exist at register
        time. Events already published land only in history; events
        published after registration land in the queue. We replay history
        up to the snapshot index, then tail the queue.
        """
        q: asyncio.Queue[Event | None] = asyncio.Queue()
        self._subs.setdefault(run_id, set()).add(q)
        # Snapshot the history AFTER registering so no event is missed;
        # the queue will collect anything published after this point.
        hist_snapshot = list(self._history.get(run_id, []))
        replayed_ids = {id(ev) for ev in hist_snapshot}
        try:
            for ev in hist_snapshot:
                yield ev
                if ev.type == "run_done":
                    return
            while True:
                ev = await q.get()
                if ev is None:
                    return
                # dedup: skip events already replayed
                if id(ev) in replayed_ids:
                    continue
                yield ev
                if ev.type == "run_done":
                    return
        finally:
            subs = self._subs.get(run_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subs.pop(run_id, None)

    def close_run(self, run_id: str) -> None:
        """Forcibly terminate all subscribers for a run (used on crash)."""
        for q in list(self._subs.get(run_id, ())):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover
                pass


class Emitter:
    """Bound to a specific run + ticket, so callers don't pass run_id around.

    `start_ts` is a monotonic reference so `ts_ms` is always >= 0 and
    strictly increasing for a single run.
    """

    def __init__(self, bus: EventBus, run_id: str, start_ts: float) -> None:
        self._bus = bus
        self._run_id = run_id
        self._start_ts = start_ts
        self._ticket_id: str | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    def bind_ticket(self, ticket_id: str) -> "Emitter":
        """Return a child emitter pinned to a ticket.

        (Cheap wrapper so multiple tickets running concurrently under the
        same run don't clobber each other's ticket_id.)
        """
        child = Emitter(self._bus, self._run_id, self._start_ts)
        child._ticket_id = ticket_id
        return child

    def emit(self, type: str, **payload: Any) -> None:
        ev = Event(
            run_id=self._run_id,
            ticket_id=self._ticket_id,
            type=type,
            ts_ms=int((time.monotonic() - self._start_ts) * 1000),
            payload=payload,
        )
        self._bus.publish(ev)


# Module-level singleton. One server process, one bus.
BUS = EventBus()
