"""Planning helpers for guided Orchard search responses."""

from __future__ import annotations

from orchard.query.search_contract import SearchStatus


def classify_search_query(raw: str) -> str:
    """Classify user input into a small set of search intent kinds."""
    if "::" in raw and "(" in raw and ")" in raw:
        return "frame"
    if "::" in raw:
        return "qualified_symbol"
    return "symbol"


def _candidate_key(raw: str, row: dict, target: str = "", language: str = "") -> tuple:
    """Sort candidates using the phase-1 bounded tie-break rules."""
    name = row["name"]
    return (
        0 if name == raw and "::" in raw else 1,
        0 if name == raw else 1,
        0 if name == raw else 1 if name.lower() == raw.lower() else 2,
        0 if target and row.get("module") == target else 1,
        0 if language and row.get("language") == language else 1,
        0 if name.startswith(raw) else 1,
        row.get("module", ""),
        row.get("kind", ""),
        name,
        row.get("usr", ""),
    )


def rank_symbol_candidates(
    raw: str, rows: list[dict], target: str = "", language: str = ""
) -> list[dict]:
    """Rank symbol candidates deterministically using existing cheap signals."""
    return sorted(rows, key=lambda row: _candidate_key(raw, row, target, language))


def plan_search_next_actions(
    status: SearchStatus, candidates: dict[str, list], raw: str
) -> list[dict]:
    """Return compact next-step actions for agent workflows."""
    actions: list[dict] = []
    if status.freshness in {"stale", "unknown"}:
        actions.append({"tool": "orchard_refresh_index", "args": {}})
    for owner in candidates.get("owners", [])[:1]:
        actions.append({"tool": "orchard_search", "args": {"name": owner}})
    if candidates.get("text"):
        actions.append({"tool": "shell_text_search", "args": {"pattern": raw}})
    return actions[:3]
