"""Web-server focused tests for request validation and frontend adaptation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.frontend_data import _compute_stats, _customer_from_email
from app.server import app


def test_unknown_customer_id_is_stable_and_human_name_is_preserved() -> None:
    customer_a = _customer_from_email("qa.customer@email.com", None)
    customer_b = _customer_from_email("qa.customer@email.com", None)
    assert customer_a == customer_b
    assert customer_a["id"].startswith("CUST-")
    assert customer_a["name"] == "Qa Customer"
    assert customer_a["email"] == "qa.customer@email.com"
    assert customer_a["tier"] == "standard"
    assert customer_a["prior_tickets"] == 0


def test_known_customer_fixture_preserves_real_tier_and_id() -> None:
    customer = _customer_from_email("alice.turner@email.com", None)
    assert customer == {
        "id": "C001",
        "name": "Alice Turner",
        "email": "alice.turner@email.com",
        "tier": "vip",
        "prior_tickets": 0,
    }


def test_compute_stats_sums_token_totals() -> None:
    stats = _compute_stats(
        [
            {
                "outcome": "resolved",
                "decision_basis": "successful_resolution",
                "agent_confidence": 0.9,
                "failures": [],
                "recovery_attempted": False,
                "tokens_in": 120,
                "tokens_out": 30,
                "tools_used": ["get_customer", "send_reply"],
            },
            {
                "outcome": "escalated",
                "decision_basis": "tool_failure",
                "agent_confidence": 0.4,
                "failures": [{"recovered": False}],
                "recovery_attempted": True,
                "tokens_in": 50,
                "tokens_out": 10,
                "tools_used": ["get_order", "escalate"],
            },
        ]
    )
    assert stats["tokens_in"] == 170
    assert stats["tokens_out"] == 40


def test_run_rejects_invalid_mode() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/run",
        json={"mode": "bad-mode", "chaos": 0.0, "tickets": ["TKT-001"]},
    )
    assert resp.status_code == 422


def test_run_rejects_out_of_range_chaos() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/run",
        json={"mode": "rules", "chaos": 1.5, "tickets": ["TKT-001"]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Endpoint shape tests
# ---------------------------------------------------------------------------


def test_health_returns_expected_shape() -> None:
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "modes" in data
    assert data["modes"]["rules"] is True  # rules is always available
    assert "llm_provider" in data


def test_snapshot_returns_valid_shape() -> None:
    client = TestClient(app)
    resp = client.get("/api/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "meta" in data
    assert "tickets" in data
    assert "stats" in data
    assert "tools" in data
    assert isinstance(data["tickets"], list)


def test_snapshot_bogus_run_id_falls_back_gracefully() -> None:
    """A stale/unknown run_id must return 200 (graceful fallback), never 500."""
    client = TestClient(app)
    resp = client.get("/api/snapshot?run_id=bogus-id-that-does-not-exist")
    assert resp.status_code == 200
    data = resp.json()
    # Still returns valid shape (fell back to clean audit_log.json)
    assert "tickets" in data
    assert isinstance(data["tickets"], list)


def test_tickets_endpoint_returns_list() -> None:
    client = TestClient(app)
    resp = client.get("/api/tickets")
    assert resp.status_code == 200
    data = resp.json()
    assert "tickets" in data
    tickets = data["tickets"]
    assert isinstance(tickets, list)
    assert len(tickets) > 0  # fixtures must be non-empty
    # Each ticket must have at minimum an id field
    assert "id" in tickets[0]


def test_dlq_returns_list_when_no_file() -> None:
    """When no DLQ file exists the endpoint returns an empty list, not an error."""
    client = TestClient(app)
    resp = client.get("/api/dlq")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_run_returns_run_id_and_count() -> None:
    """POST /api/run with a single ticket should return run_id + ticket_count."""
    client = TestClient(app)
    resp = client.post(
        "/api/run",
        json={"mode": "rules", "chaos": 0.0, "tickets": ["TKT-001"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert isinstance(data["run_id"], str)
    assert len(data["run_id"]) > 0
    assert data["ticket_count"] == 1
