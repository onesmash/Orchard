"""Single-frame lookup helpers for deterministic symbol graph enrichment."""

from __future__ import annotations

import re

from orchard.query.annotations import annotate_symbol_source_scope, execution_boundary_for, source_scope_for
from orchard.query.search_contract import SearchResponse, SearchStatus

_CXX_FRAME_RE = re.compile(
    r"(?P<qualified>[A-Za-z_~][\w:~<>$]*::[A-Za-z_~][\w:~<>$]*)"
    r"\((?P<signature>[^)]*)\)"
    r"(?:\s+\((?P<source>[^:)]+)(?::(?P<line>\d+))?\))?"
)
_OBJC_FRAME_RE = re.compile(
    r"(?P<qualified>(?P<dispatch>[-+])\[(?P<owner>[A-Za-z_][\w.$]*)"
    r"(?:\([^)]+\))?\s+(?P<symbol>[A-Za-z_][\w:]*:?)\])"
    r"(?:\s+\+\s+\d+)?"
    r"(?:\s+\((?P<source>[^:)]+)(?::(?P<line>\d+))?\))?"
)
_SWIFT_FRAME_RE = re.compile(
    r"(?P<qualified>(?P<module>[A-Za-z_][\w]*)\."
    r"(?P<owner>[A-Za-z_][\w]*)\."
    r"(?P<symbol>[A-Za-z_~][\w~]*(?:\([^)]*\))?))"
    r"(?:\s+\+\s+\d+)?"
    r"(?:\s+\((?P<source>[^:)]+)(?::(?P<line>\d+))?\))?"
)
_FRAME_RES = (_CXX_FRAME_RE, _OBJC_FRAME_RE, _SWIFT_FRAME_RE)

_DISPATCH_BOUNDARY_NAMES = {
    "process_msg",
    "sendAction",
    "postNotificationName",
    "objc_msgSend",
}


def _is_multi_line_input(raw: str) -> bool:
    return len([line for line in raw.splitlines() if line.strip()]) > 1


def parse_frame_text(raw: str) -> dict[str, str] | None:
    """Extract owner/symbol/signature from one frame-like text string."""
    if _is_multi_line_input(raw):
        return None
    return _parse_frame_line(raw.strip())


def _parse_frame_line(line: str) -> dict[str, str] | None:
    cxx_match = _CXX_FRAME_RE.search(line)
    if cxx_match:
        qualified = cxx_match.group("qualified")
        parts = qualified.split("::")
        parsed = {
            "qualified_name": qualified,
            "owner": parts[-2],
            "symbol": parts[-1],
            "signature": cxx_match.group("signature") or "",
        }
        return _with_source_info(parsed, cxx_match)

    objc_match = _OBJC_FRAME_RE.search(line)
    if objc_match:
        parsed = {
            "qualified_name": objc_match.group("qualified"),
            "owner": objc_match.group("owner").split(".")[-1],
            "symbol": objc_match.group("symbol"),
            "signature": "",
            "language_hint": "objc",
        }
        return _with_source_info(parsed, objc_match)

    swift_match = _SWIFT_FRAME_RE.search(line)
    if swift_match:
        parsed = {
            "qualified_name": swift_match.group("qualified"),
            "owner": swift_match.group("owner"),
            "symbol": swift_match.group("symbol"),
            "signature": "",
            "language_hint": "swift",
        }
        return _with_source_info(parsed, swift_match)

    return None


def _with_source_info(parsed: dict[str, str], match) -> dict[str, str]:
    if match.group("source"):
        parsed["source_file"] = match.group("source") or ""
    if match.group("line"):
        parsed["source_line"] = match.group("line") or ""
    return parsed


def lookup_frame(
    conn, raw: str, target: str = "", language: str = "", freshness: str = "unknown"
) -> dict[str, object]:
    """Perform a compact frame-oriented lookup with owner+method fallback."""
    if _is_multi_line_input(raw):
        return SearchResponse(
            query={"raw": raw, "kind": "frame"},
            status=SearchStatus(
                outcome="parse_failed", coverage="unknown", freshness=freshness
            ),
            matches=[],
            diag=["input_too_broad"],
            candidates={"symbols": [], "owners": [], "text": [], "frames": []},
            next_actions=[],
        ).to_dict()

    parsed = parse_frame_text(raw)
    if parsed is None:
        return SearchResponse(
            query={"raw": raw, "kind": "frame"},
            status=SearchStatus(
                outcome="parse_failed", coverage="unknown", freshness=freshness
            ),
            matches=[],
            diag=["frame_lookup_recommended"],
            candidates={"symbols": [], "owners": [], "text": [raw], "frames": []},
            next_actions=[{"tool": "shell_text_search", "args": {"pattern": raw}}],
        ).to_dict()

    owners = _query_owners(conn, parsed["owner"], target, language)
    methods = _query_methods(conn, parsed, owners, target, language)
    workspace_root = _workspace_root(conn)
    owners = [annotate_symbol_source_scope(owner, workspace_root) for owner in owners]
    methods = [annotate_symbol_source_scope(method, workspace_root) for method in methods]
    for entry in [*owners, *methods]:
        boundary = execution_boundary_for(entry)
        if boundary:
            entry["execution_boundary"] = boundary
    callers = _query_direct_callers(conn, methods[0]["usr"], workspace_root) if methods else []
    source_candidates = _query_source_candidates(conn, parsed.get("source_file", ""), target)
    source_candidates = [
        {
            **candidate,
            "source_scope": source_scope_for(candidate.get("file_path", ""), workspace_root),
        }
        for candidate in source_candidates
    ]

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
            freshness=freshness,
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
        "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path, "
        "s.container_usr LIMIT 5",
        params,
    ).get_all()
    return sorted((_row_to_symbol(row) for row in rows), key=_owner_rank)


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
                "RETURN s.usr, s.name, s.kind, s.language, s.module, "
                "s.file_path, s.container_usr LIMIT 5",
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
            "RETURN s.usr, s.name, s.kind, s.language, s.module, "
            "s.file_path, s.container_usr LIMIT 5",
            params,
        ).get_all()
    symbols = [_row_to_symbol(row) for row in rows]
    return sorted(symbols, key=lambda symbol: _method_rank(symbol, parsed, owners))


def _query_direct_callers(conn, usr: str, workspace_root: str = "") -> list[dict]:
    rows = conn.execute(
        "MATCH (caller:Symbol)-[r:Calls]->(target:Symbol) WHERE target.usr = $usr "
        "RETURN DISTINCT caller.usr, caller.name, caller.kind, caller.language, "
        "caller.module, caller.file_path, r.reason LIMIT 10",
        {"usr": usr},
    ).get_all()
    callers = [
        annotate_symbol_source_scope(
            {
            "usr": row[0],
            "name": row[1],
            "kind": row[2],
            "language": row[3],
            "module": row[4],
            "file_path": row[5] or "",
            "reason": row[6] or "unknown",
            },
            workspace_root,
        )
        for row in rows
    ]
    for caller in callers:
        boundary = execution_boundary_for(caller)
        if boundary:
            caller["execution_boundary"] = boundary
            caller["call_style"] = "async_or_callback_boundary"
        else:
            caller["call_style"] = "synchronous_call"
    return callers


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
    symbol = {
        "usr": row[0],
        "name": row[1],
        "kind": row[2],
        "language": row[3],
        "module": row[4],
        "file_path": row[5] or "",
    }
    if len(row) > 6:
        symbol["container_usr"] = row[6] or ""
    return symbol


def _owner_rank(symbol: dict) -> tuple[int, str]:
    kind = (symbol.get("kind") or "").lower()
    type_rank = 0 if any(token in kind for token in ("class", "struct", "record")) else 1
    return (type_rank, symbol.get("usr") or "")


def _method_rank(symbol: dict, parsed: dict[str, str], owners: list[dict]) -> tuple[int, str]:
    usr = symbol.get("usr") or ""
    container_usr = symbol.get("container_usr") or ""
    owner_usrs = {owner.get("usr") for owner in owners if owner.get("usr")}
    expected_owner_usrs = _expected_owner_usrs(parsed)
    score = 0
    if container_usr in owner_usrs:
        score += 100
    if any(
        container_usr == expected_owner_usr
        or usr.startswith(f"{expected_owner_usr}@")
        or usr.startswith(f"{expected_owner_usr}(")
        or usr.startswith(f"{expected_owner_usr}.")
        for expected_owner_usr in expected_owner_usrs
    ):
        score += 90
    if parsed["owner"] in container_usr or parsed["owner"] in usr:
        score += 20
    namespace = _namespace_prefix(parsed)
    if namespace and namespace in usr:
        score += 10
    if symbol.get("name") == parsed["symbol"]:
        score += 5
    return (-score, usr)


def _expected_owner_usrs(parsed: dict[str, str]) -> list[str]:
    if parsed.get("language_hint") == "objc":
        owner = parsed["owner"]
        return [f"c:objc(cs){owner}"]
    if parsed.get("language_hint") == "swift":
        parts = parsed["qualified_name"].split(".")
        if len(parts) >= 3:
            module = parts[0]
            owner = parts[-2]
            return [f"s:{module}.{owner}", f"s:{owner}"]
        return []
    cxx_usr = _expected_cxx_owner_usr(parsed)
    return [cxx_usr] if cxx_usr else []


def _expected_cxx_owner_usr(parsed: dict[str, str]) -> str:
    parts = parsed["qualified_name"].split("::")
    if len(parts) < 3:
        return ""
    namespaces = parts[:-2]
    owner = parts[-2]
    return "c:" + "".join(f"@N@{part}" for part in namespaces) + f"@S@{owner}"


def _namespace_prefix(parsed: dict[str, str]) -> str:
    parts = parsed["qualified_name"].split("::")
    if len(parts) < 3:
        return ""
    return "".join(f"@N@{part}" for part in parts[:-2])


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


def _workspace_root(conn) -> str:
    rows = conn.execute(
        "MATCH (b:BuildSnapshot) RETURN b.workspace_root ORDER BY b.created_at DESC LIMIT 1"
    ).get_all()
    return rows[0][0] if rows and rows[0][0] else ""


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
