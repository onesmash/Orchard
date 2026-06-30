"""Compact response contract helpers for guided Orchard search."""

from __future__ import annotations

from dataclasses import dataclass

_OUTCOMES = {"match", "ambiguous", "near_match", "no_match", "parse_failed"}
_COVERAGE = {"covered", "partial", "uncovered", "unknown"}
_FRESHNESS = {"fresh", "stale", "partially_stale", "unknown"}


@dataclass(frozen=True)
class SearchStatus:
    """Compact search result status used by guided search responses."""

    outcome: str
    coverage: str
    freshness: str

    def __post_init__(self):
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"unsupported outcome: {self.outcome}")
        if self.coverage not in _COVERAGE:
            raise ValueError(f"unsupported coverage: {self.coverage}")
        if self.freshness not in _FRESHNESS:
            raise ValueError(f"unsupported freshness: {self.freshness}")


@dataclass(frozen=True)
class SearchResponse:
    """Serializable compact search response for MCP and CLI surfaces."""

    query: dict
    status: SearchStatus
    matches: list[dict]
    diag: list[str]
    candidates: dict[str, list]
    next_actions: list[dict]

    def to_dict(self) -> dict[str, object]:
        """Return the compact JSON-ready response shape."""
        return {
            "query": self.query,
            "status": {
                "outcome": self.status.outcome,
                "coverage": self.status.coverage,
                "freshness": self.status.freshness,
            },
            "matches": self.matches,
            "diag": self.diag,
            "candidates": self.candidates,
            "next": self.next_actions,
        }
