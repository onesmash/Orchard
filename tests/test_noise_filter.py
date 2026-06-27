"""Tests for orchard.query.noise_filter — C++ operator and logging noise detection."""

import pytest
from orchard.query.noise_filter import is_noise, filter_noise


# ── Operator noise (should be filtered) ──────────────────────────────────
@pytest.mark.parametrize("name", [
    # Basic operators — exact match in current set
    "operator<<", "operator>>", "operator->", "operator()", "operator[]",
    "operator=", "operator+", "operator-", "operator*", "operator/",
    "operator%", "operator^", "operator|", "operator~", "operator!",
    "operator<", "operator>", "operator,", "operator->*", "operator<=>",
    "operator&",
    # VARIANTS NOT in the current exact-match set (the gap we're fixing)
    "operator++",
    "operator--",
    "operator+=",
    "operator-=",
    "operator*=",
    "operator/=",
    "operator%=",
    "operator^=",
    "operator|=",
    "operator<<=",
    "operator>>=",
    "operator&=",
    "operator<=",
    "operator>=",
    "operator!=",
    "operator==",
    "operator<=>",
    "operator->*",
    # Multi-word (prefix-matched in current set)
    "operator new",
    "operator new(unsigned long)",
    "operator delete",
    "operator delete(void*)",
    "operator bool",
])
def test_is_noise_operator(name):
    assert is_noise(name), f"'{name}' should be detected as noise"


# ── Logging / helper noise (should be filtered) ──────────────────────────
@pytest.mark.parametrize("name", [
    "GetMinLogLevel", "LogMessage", "LogMessageVoidify",
    "defaultCenter", "postNotificationName:object:",
    "StringPiece", "basic_stringstream", "NSLog",
    "c_str", "str", "stream",
])
def test_is_noise_helpers(name):
    assert is_noise(name), f"'{name}' should be detected as noise"


# ── NOT noise (must survive) ─────────────────────────────────────────────
@pytest.mark.parametrize("name", [
    # Business code — must not be filtered
    "viewDidLoad",
    "initWithFrame:",
    "setupUI",
    "handleButtonTap",
    "configureWithModel:",
    # C++ functions that are NOT operators
    "operatorName",            # function named "operatorName" — not an operator
    "operatorOverloaded",      # descriptive name, not an operator
    # ObjC selectors
    "tableView:numberOfRowsInSection:",
    "application:didFinishLaunchingWithOptions:",
])
def test_is_not_noise(name):
    assert not is_noise(name), f"'{name}' should NOT be detected as noise"


# ── filter_noise integration ─────────────────────────────────────────────
def test_filter_noise_removes_noise_and_counts():
    items = [
        {"name": "viewDidLoad"},
        {"name": "operator++"},
        {"name": "setupUI"},
        {"name": "operator new(unsigned long)"},
        {"name": "NSLog"},
        {"name": "handleButtonTap"},
        {"name": "operatorName"},
    ]
    filtered, removed = filter_noise(items)
    assert len(filtered) == 4  # viewDidLoad, setupUI, handleButtonTap, operatorName
    assert removed == 3         # operator++, operator new, NSLog


def test_filter_noise_all_clean():
    items = [{"name": "foo"}, {"name": "bar"}]
    filtered, removed = filter_noise(items)
    assert filtered == items
    assert removed == 0


def test_filter_noise_all_noise():
    items = [{"name": "operator<<"}, {"name": "NSLog"}]
    filtered, removed = filter_noise(items)
    assert filtered == []
    assert removed == 2


def test_filter_noise_uses_custom_key():
    items = [{"label": "viewDidLoad"}, {"label": "operator<<"}]
    filtered, removed = filter_noise(items, name_key="label")
    assert len(filtered) == 1
    assert filtered[0]["label"] == "viewDidLoad"
    assert removed == 1
