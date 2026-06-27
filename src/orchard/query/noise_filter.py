"""C++ operator noise filter for callers / callees results.

When analyzing call-graph results for iOS / macOS codebases, C++ overloaded
operators and standard-library helpers can dominate the output.  This module
provides a configurable filter that strips out known noise symbols.

Usage::

    from orchard.query.noise_filter import filter_noise
    filtered, removed = filter_noise(results)
"""

from __future__ import annotations

import re

# C++ operator noise: matched by prefix.  Any symbol whose name starts with
# "operator" followed by a non-letter character is an overloaded operator
# (``operator<<``, ``operator++``, ``operator<=>``, ``operator new``, etc.).
# Symbols like ``operatorName`` (letter directly after "operator") are NOT
# matched — those are ordinary functions with "operator" in their name.
_CPP_OPERATOR_RE = re.compile(r"^operator(?:[^a-zA-Z]|$)")

# Exact-match noise: logging / NSNotification / stream / C++ helpers.
_CPP_HELPER_NAMES = frozenset({
    "GetMinLogLevel", "LogMessage", "LogMessageVoidify",
    "defaultCenter", "postNotificationName:object:",
    "StringPiece", "basic_stringstream", "NSLog",
    "c_str", "str", "stream",
})


def is_noise(name: str) -> bool:
    """Return True if *name* is a known noise symbol.

    Matches:
      - C++ overloaded operators (``operator<<``, ``operator++``, ``operator new``, …)
      - C++ logging / stream / ObjC notification helpers
    """
    if name in _CPP_HELPER_NAMES:
        return True
    if _CPP_OPERATOR_RE.match(name):
        return True
    return False


def filter_noise(items: list[dict], name_key: str = "name") -> tuple[list[dict], int]:
    """Remove noise items from *items*, returning (filtered_list, removed_count)."""
    filtered = [item for item in items if not is_noise(item.get(name_key, ""))]
    removed = len(items) - len(filtered)
    return filtered, removed
