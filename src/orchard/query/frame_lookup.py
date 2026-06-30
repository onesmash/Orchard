"""Frame-oriented lookup helpers for crash debugging workflows."""

from __future__ import annotations

from orchard.query.search_contract import SearchResponse, SearchStatus


def parse_frame_text(raw: str) -> dict[str, str] | None:
    """Extract a minimal owner/symbol/signature tuple from stack-frame text."""
    if "::" not in raw or "(" not in raw or ")" not in raw:
        return None
    head, _, tail = raw.partition("(")
    signature = tail.rsplit(")", 1)[0]
    parts = head.split("::")
    if len(parts) < 2:
        return None
    return {
        "qualified_name": head,
        "owner": parts[-2],
        "symbol": parts[-1],
        "signature": signature,
    }


def lookup_frame(conn, raw: str, target: str = "", language: str = "") -> dict[str, object]:
    """Perform a compact frame-oriented lookup with owner fallback."""
    parsed = parse_frame_text(raw)
    if parsed is None:
        return SearchResponse(
            query={"raw": raw, "kind": "frame"},
            status=SearchStatus(
                outcome="parse_failed", coverage="unknown", freshness="unknown"
            ),
            matches=[],
            diag=["frame_lookup_recommended"],
            candidates={"symbols": [], "owners": [], "text": [raw], "frames": []},
            next_actions=[{"tool": "shell_text_search", "args": {"pattern": raw}}],
        ).to_dict()

    owner_rows = conn.execute(
        "MATCH (s:Symbol) WHERE s.name = $name "
        "RETURN s.usr, s.name, s.kind, s.language, s.module LIMIT 5",
        {"name": parsed["owner"]},
    ).get_all()
    owners = [row[1] for row in owner_rows]

    return SearchResponse(
        query={"raw": raw, "kind": "frame"},
        status=SearchStatus(
            outcome="near_match" if owners else "no_match",
            coverage="partial",
            freshness="unknown",
        ),
        matches=[],
        diag=[] if owners else ["frame_outside_index_scope"],
        candidates={
            "symbols": [],
            "owners": owners[:3],
            "text": [parsed["symbol"]],
            "frames": [parsed],
        },
        next_actions=(
            [{"tool": "orchard_search", "args": {"name": owners[0]}}]
            if owners
            else [{"tool": "shell_text_search", "args": {"pattern": parsed["symbol"]}}]
        ),
    ).to_dict()
