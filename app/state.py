"""TicketState — the single mutable object threaded through the agent loop."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models import Category, Failure, ReasoningStep, Ticket, Urgency


@dataclass
class TicketState:
    ticket: Ticket
    category: Category = "ambiguous"
    urgency: Urgency = "low"
    confidence: float = 1.0
    classifier_confidence: Optional[float] = None
    evidence_confidence: Optional[float] = None
    action_confidence: Optional[float] = None
    tools_used: list[str] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    recovery_attempted: bool = False
    reasoning_trace: list[ReasoningStep] = field(default_factory=list)
    intends_irreversible: bool = False
    outcome: str = ""

    # Data gathered during ACT
    cache: dict[str, Any] = field(default_factory=dict)

    # Outputs
    reply_sent: Optional[str] = None
    escalation_summary: Optional[str] = None
    escalation_brief: Optional[str] = None  # Fix 8: human-readable escalation brief

    # LLM call tracking (Fix 6) — 0 in rules mode, >0 in hybrid/llm
    llm_calls: int = 0
    # Token accounting — populated by registry.call_llm_structured from the
    # transport's `usage` payload. Always 0 when no real HTTP call happens
    # (rules mode, mocked tests that patch the wrapper directly).
    tokens_in: int = 0
    tokens_out: int = 0

    # Timing
    started_at: float = field(default_factory=time.time)

    # Optional realtime emitter (web UI only; None for CLI/tests/verifier).
    # Typed as Any to avoid importing app.events into the CLI's hot path —
    # the server sets this field from its own process.
    emitter: Optional[Any] = None

    def log(self, step: str, note: str) -> None:
        self.reasoning_trace.append(ReasoningStep(step=step, note=note))
        if self.emitter is not None:
            self.emitter.emit("trace", step=step, note=note)

    def record_failure(self, f: Failure) -> None:
        self.failures.append(f)

    def any_unrecovered(self) -> bool:
        return any(not f.recovered for f in self.failures)
