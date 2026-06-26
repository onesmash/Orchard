"""C++ operator noise filter for callers / callees results.

When analyzing call-graph results for iOS / macOS codebases, C++ overloaded
operators and standard-library helpers can dominate the output.  This module
provides a configurable filter that strips out known noise symbols.

Usage::

    from orchard.query.noise_filter import filter_noise
    filtered, removed = filter_noise(results)
"""

from __future__ import annotations

# C++ noise operators: exact-matched (call-graph names are operator<<, not
# operator<<char, traits>).  When a parameterised variant does appear it
# will be caught by the multi-word prefix branch below.
_CPP_NOISE_OPERATORS = {
    "operator<<", "operator>>", "operator&", "operator->",
    "operator()", "operator[]", "operator=", "operator+",
    "operator-", "operator*", "operator/", "operator%",
    "operator^", "operator|", "operator~", "operator!",
    "operator<", "operator>", "operator,", "operator->*",
    "operator<=>",
}

# Multi-word noise operators (prefix-matched — may carry template / param
# suffixes like ``operator new(unsigned long)``).
_CPP_NOISE_PREFIXES = [
    "operator new", "operator delete", "operator bool",
]

# Exact-match noise: logging / NSNotification / stream / C++ helpers
CPP_NOISE_EXACT = {
    "GetMinLogLevel", "LogMessage", "LogMessageVoidify",
    "defaultCenter", "postNotificationName:object:",
    "StringPiece", "basic_stringstream", "NSLog",
    "c_str", "str", "stream",
}


def is_noise(name: str) -> bool:
    """Return True if *name* is a known noise symbol.

    Checks exact-match operators, multi-word prefix operators, and exact
    helpers.  Call-graph names are simple operator names (``operator<<``)
    without template arguments, so exact matching is sufficient.
    """
    if name in CPP_NOISE_EXACT:
        return True
    if name in _CPP_NOISE_OPERATORS:
        return True
    for prefix in _CPP_NOISE_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def filter_noise(items: list[dict], name_key: str = "name") -> tuple[list[dict], int]:
    """Remove noise items from *items*, returning (filtered_list, removed_count)."""
    filtered = [item for item in items if not is_noise(item.get(name_key, ""))]
    removed = len(items) - len(filtered)
    return filtered, removed
