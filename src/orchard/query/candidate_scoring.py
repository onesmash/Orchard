"""Candidate scoring for symbol disambiguation.

Used by orchard_context to rank candidates when multiple symbols match
a name query.  Portable scoring — no DB access required.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Kind priority table (higher = more likely the user's intended target)
# ---------------------------------------------------------------------------
KIND_PRIORITY: dict[str, int] = {
    "class": 5,
    "struct": 4,
    "enum": 4,
    "protocol": 4,
    "interface": 4,
    "function": 3,
    "method": 2,
    "constructor": 1,
}
"""Priority bonus per kind — applied only when no explicit kind hint is given."""

AUTO_SELECT_THRESHOLD = 0.95
"""Minimum score for the top candidate to qualify for automatic selection."""

AUTO_SELECT_DELTA = 0.10
"""Minimum score gap between the top candidate and the runner-up for auto-select."""

MAX_CANDIDATES = 20
"""Hard cap on the number of candidates returned in ambiguous results."""


def score_candidate(
    candidate: dict,
    hints: dict | None = None,
) -> float:
    """Score a symbol candidate against optional disambiguation hints.

    Parameters
    ----------
    candidate:
        Dict with keys ``name``, ``kind``, ``file_path``, and optionally
        ``module``.  Values may be empty strings.
    hints:
        Optional dict with keys ``file_path``, ``kind``, ``module``.

    Returns
    -------
    float in [0.0, 1.0].  Higher = better match.
    """
    hints = hints or {}
    score = 0.50  # base — candidate matched the name query

    # --- file_path hint -------------------------------------------------------
    hint_fp = (hints.get("file_path") or "").lower()
    cand_fp = (candidate.get("file_path") or "").lower()
    if hint_fp and cand_fp and hint_fp in cand_fp:
        score += 0.40

    # --- kind hint ------------------------------------------------------------
    hint_kind = (hints.get("kind") or "").lower()
    cand_kind = (candidate.get("kind") or "").lower()
    if hint_kind and cand_kind and hint_kind == cand_kind:
        score += 0.20

    # --- module hint ----------------------------------------------------------
    hint_mod = (hints.get("module") or "").lower()
    cand_mod = (candidate.get("module") or "").lower()
    if hint_mod and cand_mod and hint_mod == cand_mod:
        score += 0.10

    # --- kind priority bonus (only when no explicit kind hint) ----------------
    if not hint_kind:
        score += KIND_PRIORITY.get(cand_kind, 0) * 0.02

    return min(1.0, score)


def resolve_candidates(
    candidates: list[dict],
    hints: dict | None = None,
    *,
    threshold: float = AUTO_SELECT_THRESHOLD,
    delta: float = AUTO_SELECT_DELTA,
    max_candidates: int = MAX_CANDIDATES,
) -> dict:
    """Score and resolve a list of candidates to *found* or *ambiguous*.

    Returns a dict with ``status``:

    ``"found"``
        Exactly one candidate either because there was only one result or
        because the top candidate's score ≥ *threshold* **and** its lead
        over the runner-up exceeds *delta*.

    ``"ambiguous"``
        Multiple viable candidates — each annotated with a ``score`` field.

    ``"not_found"``
        Empty candidate list.
    """
    if not candidates:
        return {"status": "not_found"}

    # Truncate before scoring so we never score more than the cap.
    truncated = candidates[:max_candidates]

    scored = [
        {**c, "score": round(score_candidate(c, hints), 2)}
        for c in truncated
    ]
    scored.sort(key=lambda c: c["score"], reverse=True)

    if len(scored) == 1:
        return {"status": "found", "symbol": _strip_score(scored[0])}

    top, runner_up = scored[0], scored[1]
    if top["score"] >= threshold and (top["score"] - runner_up["score"]) > delta:
        return {"status": "found", "symbol": _strip_score(top)}

    return {
        "status": "ambiguous",
        "candidates": scored,
        "truncated": len(candidates) > max_candidates,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _strip_score(candidate: dict) -> dict:
    """Return a copy of *candidate* without the internal ``score`` key."""
    return {k: v for k, v in candidate.items() if k != "score"}
