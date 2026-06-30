"""Frame-oriented lookup helpers for crash debugging workflows."""

from __future__ import annotations

import re

from orchard.query.search_contract import SearchResponse, SearchStatus

_FRAME_RE = re.compile(
    r"(?P<qualified>[A-Za-z_~][\w:~<>$]*::[A-Za-z_~][\w:~<>$]*)"
    r"\((?P<signature>[^)]*)\)"
    r"(?:\s+\((?P<source>[^:)]+)(?::(?P<line>\d+))?\))?"
)

_DISPATCH_BOUNDARY_NAMES = {
    "process_msg",
    "sendAction",
    "postNotificationName",
    "objc_msgSend",
}


def parse_frame_text(raw: str) -> dict[str, str] | None:
    """Extract a minimal owner/symbol/signature tuple from stack-frame text."""
    match = None
    for line in raw.splitlines() or [raw]:
        match = _FRAME_RE.search(line.strip())
        if match:
            break
    if not match:
        return None

    qualified = match.group("qualified")
    parts = qualified.split("::")
    parsed = {
        "qualified_name": qualified,
        "owner": parts[-2],
        "symbol": parts[-1],
        "signature": match.group("signature") or "",
    }
    if match.group("source"):
        parsed["source_file"] = match.group("source") or ""
    if match.group("line"):
        parsed["source_line"] = match.group("line") or ""
    return parsed


def lookup_frame(conn, raw: str, target: str = "", language: str = "") -> dict[str, object]:
    """Perform a compact frame-oriented lookup with owner+method fallback."""
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

    owners = _query_owners(conn, parsed["owner"], target, language)
    methods = _query_methods(conn, parsed, owners, target, language)
    callers = _query_direct_callers(conn, methods[0]["usr"]) if methods else []
    source_candidates = _query_source_candidates(conn, parsed.get("source_file", ""), target)

    diag = _diagnostics(raw, owners, methods, callers, source_candidates)
    response = SearchResponse(
        query={"raw": raw, "kind": "frame", "parsed": parsed},
        status=SearchStatus(
            outcome="match" if methods else "near_match" if owners else "no_match",
            coverage="covered"
            if methods
            else "partial"
            if owners or source_candidates
            else "unknown",
            freshness="unknown",
        ),
        matches=methods[:5],
        diag=diag,
        candidates={
            "symbols": methods[:5],
            "owners": [row["name"] for row in owners[:3]],
            "text": [parsed["symbol"]],
            "frames": [parsed],
            "source_files": source_candidates[:3],
        },
        next_actions=_next_actions(parsed, owners, methods),
    ).to_dict()
    response["resolution"] = {
        "owner": owners[0] if owners else None,
        "method": methods[0] if methods else None,
        "strategy": "owner_symbol_fallback"
        if methods and owners
        else "direct_symbol"
        if methods
        else "owner_only"
        if owners
        else "unresolved",
    }
    response["caller_summary"] = {"direct_callers": callers[:5], "count": len(callers)}
    response["notes"] = _notes_for(raw, callers, methods, owners, source_candidates)
    return response


def lookup_crash_thread(
    conn, raw: str, target: str = "", language: str = "", limit: int = 12
) -> dict[str, object]:
    """Resolve parseable frames from a crashed thread and summarize the first hit."""
    frame_lines = _extract_frame_lines(raw)[:limit]
    frame_results = [
        lookup_frame(conn, line, target=target, language=language) for line in frame_lines
    ]
    indexed = [result for result in frame_results if result["status"]["outcome"] == "match"]
    first = indexed[0] if indexed else None
    dispatch_boundaries = [
        result["query"]["parsed"]
        for result in frame_results
        if "dispatch_boundary_in_stack" in result.get("diag", [])
    ]
    diag = _dedupe(
        code for result in frame_results for code in result.get("diag", [])
    )
    first_callers = first.get("caller_summary", {}).get("direct_callers", []) if first else []
    if first_callers and _has_hidden_caller_mismatch(raw, first_callers):
        diag = _dedupe([*diag, "graph_caller_not_in_stack"])
    summary = {
        "top_frame": frame_results[0]["resolution"]["method"]
        if frame_results and "resolution" in frame_results[0]
        else None,
        "direct_callers": first_callers,
    }
    return {
        "query": {"raw": raw, "kind": "crash_thread", "frame_count": len(frame_lines)},
        "status": {
            "outcome": "match" if first else "no_match",
            "coverage": "covered" if first else "unknown",
            "freshness": "unknown",
        },
        "frames": frame_results,
        "first_indexed_symbol": first["resolution"]["method"] if first else None,
        "dispatch_boundaries": dispatch_boundaries,
        "summary": summary,
        "diag": diag,
        "next": first.get("next", []) if first else [],
        "notes": _dedupe(
            note for result in frame_results for note in result.get("notes", [])
        ),
    }


def _query_owners(conn, owner_name: str, target: str = "", language: str = "") -> list[dict]:
    where = ["s.name = $name"]
    params: dict[str, str] = {"name": owner_name}
    if target:
        where.append("s.module = $target")
        params["target"] = target
    if language:
        where.append("s.language = $language")
        params["language"] = language
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path LIMIT 5",
        params,
    ).get_all()
    return [_row_to_symbol(row) for row in rows]


def _query_methods(
    conn, parsed: dict[str, str], owners: list[dict], target: str = "", language: str = ""
) -> list[dict]:
    rows = []
    for owner in owners:
        params = {
            "symbol": parsed["symbol"],
            "qualified": parsed["qualified_name"],
            "owner_usr": owner["usr"],
        }
        where = [
            "(s.name = $symbol OR s.name = $qualified)",
            "s.container_usr = $owner_usr",
        ]
        if target:
            where.append("s.module = $target")
            params["target"] = target
        if language:
            where.append("s.language = $language")
            params["language"] = language
        rows.extend(
            conn.execute(
                f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
                "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path LIMIT 5",
                params,
            ).get_all()
        )
    if not rows:
        params = {"symbol": parsed["symbol"], "qualified": parsed["qualified_name"]}
        where = ["s.name = $symbol OR s.name = $qualified"]
        if target:
            where.append("s.module = $target")
            params["target"] = target
        if language:
            where.append("s.language = $language")
            params["language"] = language
        rows = conn.execute(
            f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
            "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path LIMIT 5",
            params,
        ).get_all()
    return [_row_to_symbol(row) for row in rows]


def _query_direct_callers(conn, usr: str) -> list[dict]:
    rows = conn.execute(
        "MATCH (caller:Symbol)-[r:Calls]->(target:Symbol) WHERE target.usr = $usr "
        "RETURN DISTINCT caller.usr, caller.name, caller.kind, caller.language, "
        "caller.module, caller.file_path, r.reason LIMIT 10",
        {"usr": usr},
    ).get_all()
    return [
        {
            "usr": row[0],
            "name": row[1],
            "kind": row[2],
            "language": row[3],
            "module": row[4],
            "file_path": row[5] or "",
            "reason": row[6] or "unknown",
        }
        for row in rows
    ]


def _query_source_candidates(conn, source_file: str, target: str = "") -> list[dict]:
    if not source_file:
        return []
    params = {"pattern": f".*{re.escape(source_file)}.*"}
    where = ["s.file_path =~ $pattern"]
    if target:
        where.append("s.module = $target")
        params["target"] = target
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN DISTINCT s.file_path, s.module, s.language LIMIT 5",
        params,
    ).get_all()
    return [
        {"file_path": row[0] or "", "module": row[1] or "", "language": row[2] or ""}
        for row in rows
    ]


def _row_to_symbol(row) -> dict:
    return {
        "usr": row[0],
        "name": row[1],
        "kind": row[2],
        "language": row[3],
        "module": row[4],
        "file_path": row[5] or "",
    }


def _diagnostics(
    raw: str,
    owners: list[dict],
    methods: list[dict],
    callers: list[dict],
    source_candidates: list[dict],
) -> list[str]:
    diag: list[str] = []
    if methods and owners:
        diag.append("resolved_by_owner_symbol_fallback")
    elif not owners and not methods:
        diag.append("frame_outside_index_scope")
    if source_candidates and not methods:
        diag.append("source_file_seen_without_symbol_match")
    if callers and _has_hidden_caller_mismatch(raw, callers):
        diag.append("graph_caller_not_in_stack")
    if _contains_dispatch_boundary(raw):
        diag.append("dispatch_boundary_in_stack")
    return diag


def _next_actions(parsed: dict[str, str], owners: list[dict], methods: list[dict]) -> list[dict]:
    if methods:
        return [
            {"tool": "orchard_find_references", "args": {"usr": methods[0]["usr"]}},
            {"tool": "orchard_symbol", "args": {"usr": methods[0]["usr"]}},
        ]
    if owners:
        return [{"tool": "orchard_search", "args": {"name": owners[0]["name"]}}]
    return [{"tool": "shell_text_search", "args": {"pattern": parsed["symbol"]}}]


def _has_hidden_caller_mismatch(raw: str, callers: list[dict]) -> bool:
    if "\n" not in raw:
        return False
    return not any(caller["name"] and caller["name"] in raw for caller in callers)


def _contains_dispatch_boundary(raw: str) -> bool:
    return any(name in raw for name in _DISPATCH_BOUNDARY_NAMES)


def _extract_frame_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if _FRAME_RE.search(stripped):
            lines.append(stripped)
    return lines


def _dedupe(items) -> list:
    seen = set()
    result = []
    for item in items:
        marker = repr(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _notes_for(
    raw: str,
    callers: list[dict],
    methods: list[dict],
    owners: list[dict],
    source_candidates: list[dict],
) -> list[str]:
    notes: list[str] = []
    if methods and callers and _has_hidden_caller_mismatch(raw, callers):
        notes.append(
            "The compiler-indexed direct caller is absent from the pasted stack. "
            "This can happen with inlining, tail-call optimization, virtual dispatch, "
            "or an async dispatch boundary."
        )
    if _contains_dispatch_boundary(raw):
        notes.append(
            "The stack includes a likely dispatch boundary; continue from that frame "
            "to inspect the message/callback object that invoked the resolved symbol."
        )
    if source_candidates and not methods:
        notes.append(
            "A source file with the frame filename exists in indexed symbols, but no "
            "matching method symbol was resolved for this build snapshot."
        )
    if not owners and not methods:
        notes.append(
            "No indexed owner or method matched this frame; the frame may be outside "
            "the indexed target coverage."
        )
    return notes
