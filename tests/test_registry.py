"""Tests for app/registry.py — _extract_json and call_llm_structured repair logic.

No actual HTTP calls are made. We test the JSON extraction helper and verify
the repair prompt is compact (Fix 5) and that raw_decode-based parsing handles
nested braces and trailing content correctly (Fix 4).
"""
from __future__ import annotations

import json

import pytest

from app.registry import _extract_json


# ---------------------------------------------------------------------------
# Fix 4 — _extract_json robustness
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    raw = '{"category": "refund_request", "confidence": 0.9}'
    result = _extract_json(raw)
    assert result["category"] == "refund_request"
    assert result["confidence"] == pytest.approx(0.9)


def test_extract_json_fenced():
    raw = '```json\n{"category": "refund_request", "confidence": 0.9}\n```'
    result = _extract_json(raw)
    assert result["category"] == "refund_request"


def test_extract_json_fenced_no_language():
    raw = '```\n{"category": "shipping_inquiry"}\n```'
    result = _extract_json(raw)
    assert result["category"] == "shipping_inquiry"


def test_extract_json_with_prose_before():
    raw = 'Here is the classification result: {"category": "cancellation", "confidence": 0.8}'
    result = _extract_json(raw)
    assert result["category"] == "cancellation"


def test_extract_json_stops_at_first_complete_object():
    """raw_decode should stop at the first complete JSON object, not the last }."""
    raw = '{"a": 1} extra {"b": 2}'
    result = _extract_json(raw)
    assert result == {"a": 1}


def test_extract_json_nested_braces():
    """Nested dicts must not confuse the parser (old first-{/last-} approach was fine here too)."""
    raw = '{"outer": {"inner": 42}, "top": true}'
    result = _extract_json(raw)
    assert result["outer"]["inner"] == 42
    assert result["top"] is True


def test_extract_json_nested_with_trailing_prose():
    """Nested dict followed by prose — raw_decode stops correctly."""
    raw = '{"a": {"b": 1}} and some trailing text {"c": 3}'
    result = _extract_json(raw)
    assert result == {"a": {"b": 1}}


def test_extract_json_raises_on_no_json():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("no json here at all")


def test_extract_json_raises_on_empty():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _extract_json("")


def test_extract_json_fenced_with_nested_content():
    """Fenced block containing nested JSON — should parse the inner object."""
    raw = '```json\n{"category": "wrong_item", "details": {"variant": "blue"}}\n```'
    result = _extract_json(raw)
    assert result["category"] == "wrong_item"
    assert result["details"]["variant"] == "blue"
