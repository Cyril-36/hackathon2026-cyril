"""Regression tests for the dashboard shell layout."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shell_css_uses_auto_sized_dashboard_row() -> None:
    css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
    assert "grid-template-rows: 44px auto 1fr 24px;" in css
    assert "grid-template-rows: 44px 58px 1fr 24px;" not in css


def test_shell_runtime_override_keeps_dashboard_row_auto_sized() -> None:
    app_js = (ROOT / "frontend" / "components" / "app.jsx").read_text(
        encoding="utf-8"
    )
    assert (
        "gridTemplateRows: displayMeta.chaos ? '44px 24px auto 1fr 24px' : "
        "'44px auto 1fr 24px'"
    ) in app_js
    assert "gridTemplateRows: displayMeta.chaos ? '44px 24px 52px 1fr 22px'" not in app_js


def test_ticket_detail_uses_single_confidence_label_and_spaced_key_value_rows() -> None:
    detail_js = (ROOT / "frontend" / "components" / "detail.jsx").read_text(
        encoding="utf-8"
    )
    assert 'className="kv-inline"' in detail_js
    assert "fmt.pct(ticket.classified_confidence)}<ConfBar" not in detail_js
    assert "fmt.pct(ticket.agent_confidence)}<ConfBar" not in detail_js


def test_ticket_list_uses_humanized_categories_and_single_outcome_marker() -> None:
    list_js = (ROOT / "frontend" / "components" / "list.jsx").read_text(
        encoding="utf-8"
    )
    assert "Failed Queue" in list_js
    assert "humanizeCategory(t.category)" in list_js
    assert "<BasisDot basis={t.decision_basis} />" not in list_js


def test_refund_summary_does_not_claim_new_refund_when_trace_marks_it_idempotent() -> None:
    detail_js = (ROOT / "frontend" / "components" / "detail.jsx").read_text(
        encoding="utf-8"
    )
    assert "no new refund was issued because the order was already refunded" in detail_js


def test_dashboard_exposes_mode_picker_and_unified_failed_queue_copy() -> None:
    chrome_js = (ROOT / "frontend" / "components" / "chrome.jsx").read_text(
        encoding="utf-8"
    )
    assert 'className="mode-select"' in chrome_js
    assert "Failed Queue" in chrome_js
    assert "Failed Jobs" not in chrome_js


def test_topbar_uses_compact_meta_chips() -> None:
    css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
    assert ".topbar-meta .meta-chip" in css
    assert ".mode-select" in css


def test_tweaks_panel_no_longer_renders_second_mode_control() -> None:
    tweaks_js = (ROOT / "frontend" / "components" / "tweaks.jsx").read_text(
        encoding="utf-8"
    )
    assert "Agent mode" not in tweaks_js
    assert "['rules','hybrid','llm']" not in tweaks_js
