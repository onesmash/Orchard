"""Unit tests for candidate_scoring module."""

import pytest
from orchard.query.candidate_scoring import (
    score_candidate,
    resolve_candidates,
    KIND_PRIORITY,
    AUTO_SELECT_THRESHOLD,
    AUTO_SELECT_DELTA,
)


# ---------------------------------------------------------------------------
# score_candidate
# ---------------------------------------------------------------------------

def test_score_exact_match_all_hints():
    """Full match: file_path + kind + module → 1.10 (capped at 1.0)."""
    c = {"name": "foo", "kind": "class", "file_path": "/src/bar.swift", "module": "MyModule"}
    hints = {"file_path": "bar.swift", "kind": "class", "module": "MyModule"}
    assert score_candidate(c, hints) == pytest.approx(1.0)


def test_score_file_only():
    """Only file_path matches → 0.50 + 0.40 + kind priority (method 2*0.02) = 0.94."""
    c = {"name": "foo", "kind": "method", "file_path": "/src/bar.swift", "module": "Other"}
    hints = {"file_path": "bar.swift"}
    assert score_candidate(c, hints) == pytest.approx(0.94)

def test_score_name_only():
    """No hints → base 0.50 + kind bonus."""
    c = {"name": "foo", "kind": "class", "file_path": "/src/bar.swift", "module": "Other"}
    score = score_candidate(c, {})
    # base 0.50 + class priority 5 * 0.02 = 0.60
    assert score == 0.60


def test_score_module_hint():
    """Module hint → 0.50 + 0.10 + kind priority (function 3*0.02) = 0.66."""
    c = {"name": "foo", "kind": "function", "file_path": "/x.swift", "module": "MyModule"}
    hints = {"module": "MyModule"}
    assert score_candidate(c, hints) == pytest.approx(0.66)


def test_score_kind_priority_bonus():
    """Class gets higher priority than function when no kind hint."""
    c_class = {"name": "foo", "kind": "class", "file_path": "", "module": ""}
    c_func = {"name": "foo", "kind": "function", "file_path": "", "module": ""}
    assert score_candidate(c_class) > score_candidate(c_func)


def test_score_kind_hint_overrides_priority():
    """Explicit kind hint suppresses priority bonus."""
    c = {"name": "foo", "kind": "constructor", "file_path": "", "module": ""}
    hint = {"kind": "constructor"}
    s = score_candidate(c, hint)
    # base 0.50 + kind match 0.20 = 0.70 (no priority bonus since hint is set)
    assert s == pytest.approx(0.70)


def test_score_file_path_substring_case_insensitive():
    """file_path hint is case-insensitive substring match."""
    c = {"name": "foo", "kind": "", "file_path": "/SRC/BAR.swift", "module": ""}
    hints = {"file_path": "bar.swift"}
    assert score_candidate(c, hints) == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# resolve_candidates
# ---------------------------------------------------------------------------

def test_resolve_auto_select_clear_winner():
    """Top candidate ≥ 0.95 with large delta → auto-select."""
    data = [
        {"usr": "a", "name": "Foo", "kind": "class", "file_path": "/src/Foo.swift", "module": "App"},
        {"usr": "b", "name": "Foo", "kind": "method", "file_path": "/other/Bar.swift", "module": "Lib"},
    ]
    hints = {"file_path": "Foo.swift", "kind": "class"}
    result = resolve_candidates(data, hints)
    assert result["status"] == "found"
    assert result["symbol"]["usr"] == "a"


def test_resolve_ambiguous_tie():
    """Two candidates with identical scores → ambiguous."""
    data = [
        {"usr": "a", "name": "Foo", "kind": "class", "file_path": "/src/Foo.swift", "module": "App"},
        {"usr": "b", "name": "Foo", "kind": "class", "file_path": "/src/Foo.swift", "module": "App"},
    ]
    result = resolve_candidates(data)
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert all("score" in c for c in result["candidates"])


def test_resolve_single_candidate():
    """Only one candidate → found regardless of score."""
    data = [{"usr": "a", "name": "Foo", "kind": "function", "file_path": "/x.swift", "module": ""}]
    result = resolve_candidates(data)
    assert result["status"] == "found"
    assert result["symbol"]["usr"] == "a"


def test_resolve_empty():
    """Empty candidate list → not_found."""
    assert resolve_candidates([])["status"] == "not_found"


def test_resolve_truncation():
    """More than max_candidates → truncated flag."""
    data = [
        {"usr": str(i), "name": f"Foo{i}", "kind": "function", "file_path": "", "module": ""}
        for i in range(25)
    ]
    result = resolve_candidates(data, max_candidates=20)
    assert result["status"] in ("ambiguous", "found")
    assert result.get("truncated") is True
    assert len(result.get("candidates", [])) <= 20
