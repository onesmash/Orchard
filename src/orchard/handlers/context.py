"""orchard_context — 360-degree symbol view aggregator.

Returns symbol metadata, categorized incoming/outgoing references,
type hierarchy, and dynamic binding hints in a single response.
Supports tristate resolution: found / ambiguous / not_found.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.handlers.references import ReferencesRequest, find_references
from orchard.handlers.symbol_context import SymbolContextRequest, get_symbol_context
from orchard.handlers.type_hierarchy import TypeHierarchyRequest, get_type_hierarchy
from orchard.normalize.identity import make_symbol_id
from orchard.query.candidate_scoring import resolve_candidates
from orchard.query.lookup import GraphLookup
from orchard.validation.freshness import freshness_for


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = 30
"""Default per-direction cap on incoming / outgoing reference entries."""


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------

@dataclass
class ContextRequest(BaseToolRequest):
    """Request for 360-degree symbol context.

    Either *usr* or *name* must be provided.  *usr* is the preferred
    zero-ambiguity entry point; *name* triggers a fuzzy lookup with
    disambiguation support via *file_path*, *kind*, and *module* hints.
    """

    usr: str = ""
    name: str = ""
    file_path: str = ""
    kind: str = ""
    include_notification_bridges: bool = False


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def get_context(conn, req: ContextRequest) -> BaseToolResponse:
    """Return a 360-degree view of a symbol.

    Parameters
    ----------
    conn:
        Read-only Ladybug connection (used only for the initial resolution;
        parallel sub-queries open their own connections).
    req:
        ContextRequest with usr (preferred) or name + optional hints.

    Returns
    -------
    BaseToolResponse
    """
    # --- freshness -----------------------------------------------------------
    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    # --- Step 1: resolve the target symbol -----------------------------------
    if req.usr:
        resolution = _resolve_by_usr(conn, req)
    elif req.name:
        resolution = _resolve_by_name(conn, req)
    else:
        return BaseToolResponse(
            data={"status": "not_found"},
            freshness=freshness_status,
            build_id=req.build_id,
            open_gaps=["Either 'usr' or 'name' is required."],
            evidence_sources=[],
        )

    status = resolution.get("status", "not_found")

    if status != "found":
        return BaseToolResponse(
            data=resolution,
            freshness=freshness_status,
            build_id=req.build_id,
            open_gaps=resolution.get("open_gaps", []),
            evidence_sources=[],
        )

    symbol = resolution["symbol"]

    # --- Step 2: parallel sub-queries ----------------------------------------
    results, open_gaps = _parallel_gather(conn, symbol, req)

    # --- Step 3: assemble ----------------------------------------------------
    return BaseToolResponse(
        data={
            "status": "found",
            "symbol": symbol,
            **results,
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation", "indexstore_symbol_table"],
        open_gaps=open_gaps,
    )


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_by_usr(conn, req: ContextRequest) -> dict:
    """Zero-ambiguity USR lookup."""
    sym_id = make_symbol_id(req.usr)
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) "
        "RETURN s.usr, s.name, s.language, s.kind, s.module, s.file_path, "
        "s.signature, s.access_level, s.origin",
        {"id": sym_id},
    ).get_all()
    if not rows:
        return {
            "status": "not_found",
            "open_gaps": [
                f"Symbol with usr '{req.usr}' not found in index. "
                "Try orchard_search to discover available symbols, "
                "or run orchard ingest --project-dir . to refresh the index."
            ],
        }
    r = rows[0]
    return {
        "status": "found",
        "symbol": {
            "usr": r[0],
            "name": r[1],
            "language": r[2],
            "kind": r[3],
            "module": r[4],
            "file_path": r[5],
            "signature": r[6],
            "access_level": r[7],
        },
    }


def _resolve_by_name(conn, req: ContextRequest) -> dict:
    """Fuzzy name lookup with candidate disambiguation."""
    gl = GraphLookup(conn)

    # Reuse orchard_search-style pattern matching
    import re as _re
    raw = req.name
    if _re.search(r'[.*+?^$\[\](){}\\|]', raw):
        pattern = raw
    else:
        pattern = f".*{raw}.*"

    rows = conn.execute(
        "MATCH (s:Symbol) WHERE s.name =~ $pattern "
        "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path "
        "ORDER BY s.name LIMIT 25",
        {"pattern": pattern},
    ).get_all()

    if not rows:
        return {
            "status": "not_found",
            "open_gaps": [
                f"Symbol '{req.name}' not found. "
                "Try orchard_search with a broader term, "
                "or run orchard ingest --project-dir . to refresh the index."
            ],
        }

    candidates = [
        {
            "usr": r[0],
            "name": r[1],
            "kind": r[2],
            "language": r[3],
            "module": r[4],
            "file_path": r[5],
        }
        for r in rows
    ]

    hints = {}
    if req.file_path:
        hints["file_path"] = req.file_path
    if req.kind:
        hints["kind"] = req.kind
    if req.module:
        hints["module"] = req.module

    result = resolve_candidates(candidates, hints)

    # resolve_candidates already returns {status, symbol|[{score,...}]}
    if result["status"] == "not_found":
        # Shouldn't happen since we checked for empty rows above, but be safe.
        result["open_gaps"] = [
            f"Symbol '{req.name}' not found after scoring."
        ]
    return result


# ---------------------------------------------------------------------------
# Parallel sub-query execution
# ---------------------------------------------------------------------------

def _parallel_gather(conn, symbol: dict, req: ContextRequest):
    """Run references, hierarchy, and dynamic bindings in parallel.

    Each worker opens an independent read-only connection to avoid
    SQLite thread-safety issues.
    """
    from orchard.graph.db import get_connection

    # Snapshot connection params from the caller's connection
    db_path = conn.path if hasattr(conn, "path") else ""

    results: dict[str, object] = {}
    open_gaps: list[str] = []

    def _with_conn(fn, *args):
        """Open a dedicated connection for this thread, call *fn*, close."""
        if not db_path:
            # Fallback: reuse the provided conn (single-threaded path)
            return fn(conn, *args)
        c2 = get_connection(db_path, read_only=True)
        try:
            return fn(c2, *args)
        finally:
            c2.close()

    usr = symbol.get("usr", "")
    build_id = req.build_id or ""

    tasks: dict[str, callable] = {
        "incoming_outgoing": lambda: _with_conn(
            _gather_references, usr, build_id,
        ),
        "hierarchy": lambda: _with_conn(
            _gather_hierarchy, usr, build_id,
        ),
        "dynamic_binding_hints": lambda: _with_conn(
            _gather_dynamic_bindings, usr, build_id,
        ),
        "processes": lambda: _with_conn(
            _gather_processes, usr,
        ),
    }

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for f in as_completed(futures):
            name = futures[f]
            try:
                results[name] = f.result()
            except Exception as exc:
                open_gaps.append(f"{name}_failed: {exc}")

    return results, open_gaps


def _gather_references(conn, usr: str, build_id: str) -> dict:
    """Return categorized incoming / outgoing references."""
    resp = find_references(conn, ReferencesRequest(usr=usr, build_id=build_id))
    data = resp.data if isinstance(resp.data, dict) else {}
    raw_incoming: list[dict] = data.get("incoming", []) or []
    raw_outgoing: list[dict] = data.get("outgoing", []) or []

    incoming = _categorize_refs(raw_incoming)
    outgoing = _categorize_refs(raw_outgoing)

    incoming_truncated = len(raw_incoming) > DEFAULT_LIMIT
    outgoing_truncated = len(raw_outgoing) > DEFAULT_LIMIT

    return {
        "incoming": _limit_dict(incoming, DEFAULT_LIMIT),
        "outgoing": _limit_dict(outgoing, DEFAULT_LIMIT),
        "pagination": {
            "incoming_limit": DEFAULT_LIMIT,
            "outgoing_limit": DEFAULT_LIMIT,
            "incoming_truncated": incoming_truncated,
            "outgoing_truncated": outgoing_truncated,
            "incoming_total": len(raw_incoming),
            "outgoing_total": len(raw_outgoing),
        },
    }


def _gather_hierarchy(conn, usr: str, build_id: str) -> dict:
    """Return superclasses, protocols, and subclasses."""
    resp = get_type_hierarchy(
        conn, TypeHierarchyRequest(usr=usr, build_id=build_id),
    )
    if isinstance(resp.data, dict):
        return resp.data
    return {}


def _gather_dynamic_bindings(conn, usr: str, build_id: str) -> list[dict]:
    """Look for notification / target-action dynamic bindings."""
    return GraphLookup(conn).dynamic_bindings(usr) if hasattr(GraphLookup, "dynamic_bindings") else []


def _gather_processes(conn, usr: str) -> list[dict]:
    """Return execution flows the symbol participates in, with step index."""
    from orchard.normalize.identity import make_symbol_id
    sym_id = make_symbol_id(usr)
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id})-[r:STEP_IN_PROCESS]->(p:Process) "
        "RETURN p.id, p.label, p.process_type, p.step_count, p.entry_name, r.step "
        "ORDER BY r.step LIMIT 10",
        {"id": sym_id},
    ).get_all()
    return [
        {
            "id": r[0],
            "label": r[1],
            "process_type": r[2],
            "step_count": r[3],
            "entry_name": r[4],
            "step_index": r[5],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Categorisation helpers
# ---------------------------------------------------------------------------

_RELATION_TYPE_KEY = "relation_type"


def _categorize_refs(refs: list[dict]) -> dict[str, list[dict]]:
    """Group a flat list of reference dicts by relation type."""
    cats: dict[str, list[dict]] = {}
    for ref in refs:
        rt = ref.get(_RELATION_TYPE_KEY, "Calls") or "Calls"
        rt = rt.capitalize()
        cats.setdefault(rt, []).append(ref)
    return cats


def _limit_dict(
    cats: dict[str, list[dict]],
    limit: int,
) -> dict[str, list[dict]]:
    """Apply a per-category entry cap for terse output."""
    return {k: v[:limit] for k, v in cats.items()}
