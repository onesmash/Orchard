import asyncio
from dataclasses import dataclass, field
from typing import Any

from orchard.build.context import BuildContext
from orchard.build.discovery import discover_symbolgraph_paths
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.indexstore import read_index_store
from orchard.ingest.symbolgraph import parse_symbolgraph
from orchard.normalize.identity import (
    upsert_build_snapshot,
    upsert_symbols,
    upsert_symbol_rels,
    upsert_calls,
    upsert_references,
)


@dataclass
class PhaseResult:
    phase: str
    build_id: str
    data: Any
    stats: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


async def run_ingest_pipeline(ctx: BuildContext, db_path: str) -> list[PhaseResult]:
    results: list[PhaseResult] = []
    conn = get_connection(db_path)
    init_schema(conn)
    upsert_build_snapshot(conn, ctx)

    # indexstore_ingest
    is_result = None
    if ctx.index_store_path:
        is_result = read_index_store(ctx.index_store_path, ctx.target,
                                      source_root=ctx.workspace_root)
        results.append(PhaseResult(
            phase="indexstore_ingest", build_id=ctx.build_id, data=is_result,
            stats={"occurrences": len(is_result.occurrences), "relations": len(is_result.relations)},
            warnings=is_result.warnings,
        ))
    else:
        results.append(PhaseResult(
            phase="indexstore_ingest", build_id=ctx.build_id, data=None,
            warnings=["index_store_path not set; skipped"],
        ))

    # swift_symbolgraph_ingest
    sg_paths = discover_symbolgraph_paths(ctx.derived_data_path or "")
    all_symbols = []
    all_rels = []
    for path in sg_paths:
        sg = parse_symbolgraph(path, ctx.target)
        all_symbols.extend(sg.symbols)
        all_rels.extend(sg.relationships)
    results.append(PhaseResult(
        phase="swift_symbolgraph_ingest", build_id=ctx.build_id,
        data=None, stats={"symbols": len(all_symbols), "relationships": len(all_rels)},
    ))

    # identity_normalization
    inserted = upsert_symbols(conn, all_symbols, ctx.target)
    upsert_symbol_rels(conn, all_rels, ctx.target, source="swift_symbolgraph")
    results.append(PhaseResult(
        phase="identity_normalization", build_id=ctx.build_id, data=None,
        stats={"symbols_upserted": inserted},
    ))

    # call_graph_derivation — persist Calls + References edges from IndexStore
    calls_written = 0
    refs_written = 0
    if is_result is not None:
        calls_written = upsert_calls(
            conn, is_result.relations, ctx.target,
            source="indexstore", build_id=ctx.build_id,
        )
        refs_written = upsert_references(
            conn, is_result.relations, ctx.target, source="indexstore",
        )
    results.append(PhaseResult(
        phase="call_graph_derivation", build_id=ctx.build_id, data=None,
        stats={"calls_written": calls_written, "references_written": refs_written},
    ))
    conn.close()
    return results
