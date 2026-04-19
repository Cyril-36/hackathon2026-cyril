from __future__ import annotations

from app.mode_compare import build_report
from app.models import AuditEntry


def _entry(
    ticket_id: str,
    *,
    mode: str,
    outcome: str,
    basis: str,
    category: str = "refund_request",
    summary: str = "",
    reply: str = "",
) -> AuditEntry:
    return AuditEntry(
        run_id=f"run-{mode}",
        ticket_id=ticket_id,
        timestamp="2024-03-15T00:00:00Z",
        mode=mode,
        category=category,
        urgency="medium",
        outcome=outcome,
        confidence=0.8,
        classifier_confidence=0.7,
        evidence_confidence=0.8,
        action_confidence=0.9,
        tools_used=[],
        failures=[],
        recovery_attempted=False,
        decision_basis=basis,
        duration_ms=10,
        reasoning_trace=[],
        escalation_summary=summary,
        reply_sent=reply,
    )


def test_build_report_surfaces_ticket_level_differences() -> None:
    report = build_report(
        {
            "rules": [
                _entry("TKT-001", mode="rules", outcome="resolved", basis="successful_resolution"),
                _entry("TKT-002", mode="rules", outcome="resolved", basis="successful_resolution"),
            ],
            "hybrid": [
                _entry("TKT-001", mode="hybrid", outcome="resolved", basis="successful_resolution"),
                _entry("TKT-002", mode="hybrid", outcome="escalated", basis="low_confidence", summary="needs review"),
            ],
            "llm": [
                _entry("TKT-001", mode="llm", outcome="resolved", basis="successful_resolution"),
                _entry("TKT-002", mode="llm", outcome="escalated", basis="low_confidence", summary="needs review"),
            ],
        }
    )

    assert report["diff_count"] == 1
    diff = report["tickets_with_differences"][0]
    assert diff["ticket_id"] == "TKT-002"
    assert diff["modes"]["rules"]["outcome"] == "resolved"
    assert diff["modes"]["hybrid"]["decision_basis"] == "low_confidence"


def test_build_report_scores_against_expectations() -> None:
    report = build_report(
        {
            "rules": [
                _entry(
                    "TKT-001",
                    mode="rules",
                    outcome="resolved",
                    basis="successful_resolution",
                    reply="Your refund is confirmed.",
                ),
                _entry(
                    "TKT-002",
                    mode="rules",
                    outcome="escalated",
                    basis="low_confidence",
                    reply="A specialist will follow up.",
                ),
            ],
        },
        expectations={
            "TKT-001": {
                "category": "refund_request",
                "outcome": "resolved",
                "decision_basis": "successful_resolution",
                "reply_contains": ["refund"],
            },
            "TKT-002": {
                "category": "refund_request",
                "outcome": "resolved",
                "reply_contains": ["order id"],
            },
        },
    )

    summary = report["summary"]["rules"]
    assert summary["gold_matched"] == 1
    assert summary["gold_total"] == 2
    assert summary["gold_match_rate"] == 0.5
    assert summary["gold_mismatches"][0]["ticket_id"] == "TKT-002"
