import asyncio
from dataclasses import dataclass, field
from typing import Any

from orchard.build.context import BuildContext
from orchard.build.discovery import discover_symbolgraph_paths
from orchard.derive.architecture import run_architecture_derivation
from orchard.derive.bridge import run_bridge_recovery
from orchard.derive.swiftui import run_swiftui_derivation
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.indexstore import read_index_store
from orchard.ingest.symbolgraph import parse_symbolgraph
from orchard.normalize.identity import (
    upsert_build_snapshot,
    upsert_symbols,
    upsert_symbol_rels,
    upsert_calls,
    upsert_indexstore_rels,
    upsert_references,
)
from orchard.search.chunker import chunk_symbols
from orchard.search.embedder import Embedder, EmbeddingError


@dataclass
class PhaseResult:
    phase: str
    build_id: str
    data: Any
    stats: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _map_indexstore_kind(kind: str) -> str:
    """Normalise IndexStoreDB kind strings to Orchard symbol kinds."""
    k = kind.lower()
    if k in ("struct", "class", "enum", "protocol", "extension", "union"):
        return k
    if k in ("function", "constructor", "destructor"):
        return "function"
    if k in ("instancemethod", "staticmethod", "classmethod"):
        return "method"
    if k in ("instanceproperty", "staticproperty", "classproperty"):
        return "instanceProperty"
    if k in ("var", "variable", "local", "parameter"):
        return "var"
    return k  # pass through unknown kinds


async def run_ingest_pipeline(ctx: BuildContext, db_path: str) -> list[PhaseResult]:
    results: list[PhaseResult] = []
    conn = get_connection(db_path)
    init_schema(conn)
    upsert_build_snapshot(conn, ctx)

    # indexstore_ingest and swift_symbolgraph_ingest are independent I/O
    # (subprocess + file reads) — run them concurrently via asyncio.gather.
    # They don't write to the graph, so no connection contention.
    async def _run_indexstore() -> tuple[PhaseResult, object]:
        if ctx.index_store_path:
            is_res = read_index_store(ctx.index_store_path, ctx.target,
                                      source_root=ctx.workspace_root)
            return PhaseResult(
                phase="indexstore_ingest", build_id=ctx.build_id, data=is_res,
                stats={"occurrences": len(is_res.occurrences),
                       "relations": len(is_res.relations)},
                warnings=is_res.warnings,
            ), is_res
        return PhaseResult(
            phase="indexstore_ingest", build_id=ctx.build_id, data=None,
            warnings=["index_store_path not set; skipped"],
        ), None

    async def _run_symbolgraph() -> PhaseResult:
        sg_paths = discover_symbolgraph_paths(ctx.derived_data_path or "")
        symbols = []
        rels = []
        for path in sg_paths:
            sg = parse_symbolgraph(path, ctx.target)
            symbols.extend(sg.symbols)
            rels.extend(sg.relationships)
        return PhaseResult(
            phase="swift_symbolgraph_ingest", build_id=ctx.build_id,
            data=None, stats={"symbols": len(symbols), "relationships": len(rels)},
        ), symbols, rels

    (is_phase, is_result), (sg_phase, all_symbols, all_rels) = \
        await asyncio.gather(_run_indexstore(), _run_symbolgraph())
    results.append(is_phase)
    results.append(sg_phase)

    # Fallback: when no .symbols.json files are found, use IndexStore symbol
    # descriptors.  These carry real name / kind / module from the compiler,
    # just without inter-symbol structure edges (inherits, conforms, etc.).
    if not all_symbols and is_result and is_result.symbols:
        from orchard.ingest.indexstore import SymbolLineRecord
        is_syms = [
            SymbolRecord(
                usr=s.usr, precise_id="", name=s.name,
                kind=_map_indexstore_kind(s.symbol_kind),
                module=s.module, language=s.language,
                file_path="", signature="", access_level="public",
                container_usr=None,
            )
            for s in is_result.symbols
        ]
        all_symbols = is_syms

    # identity_normalization
    inserted = upsert_symbols(conn, all_symbols, ctx.target)
    upsert_symbol_rels(conn, all_rels, ctx.target, source="swift_symbolgraph")
    results.append(PhaseResult(
        phase="identity_normalization", build_id=ctx.build_id, data=None,
        stats={"symbols_upserted": inserted},
    ))

    # swiftinterface_conformances — extract ConformsTo edges from .swiftinterface
    conformances_written = 0
    if ctx.derived_data_path:
        from orchard.build.discovery import discover_swiftinterface_paths
        from orchard.ingest.swiftinterface import parse_interface_file
        si_paths = discover_swiftinterface_paths(ctx.derived_data_path)
        for sip in si_paths:
            confs = parse_interface_file(sip)
            for c in confs:
                # Match type -> Symbol by name.  Collisions are possible
                # across modules; the first name-match wins.
                sym_rows = conn.execute(
                    "MATCH (s:Symbol {target_id: $tid}) "
                    "WHERE s.name = $name RETURN s.usr",
                    {"tid": ctx.target, "name": c.type_name},
                ).get_all()
                proto_rows = conn.execute(
                    "MATCH (p:Symbol {target_id: $tid}) "
                    "WHERE p.name = $name RETURN p.usr",
                    {"tid": ctx.target, "name": c.protocol_name},
                ).get_all()
                if sym_rows and proto_rows:
                    conn.execute(
                        "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
                        "MERGE (a)-[:ConformsTo {source: 'swiftinterface'}]->(b)",
                        {"src": f"{ctx.target}:{sym_rows[0][0]}",
                         "dst": f"{ctx.target}:{proto_rows[0][0]}"},
                    )
                    conformances_written += 1
        if si_paths:
            results.append(PhaseResult(
                phase="swiftinterface_conformances", build_id=ctx.build_id,
                data=None, stats={"interfaces_parsed": len(si_paths),
                                  "conformances_written": conformances_written},
            ))

    # cross_language_bridge_recovery
    bridge_stats = run_bridge_recovery(conn, ctx.target, ctx.build_id)
    results.append(PhaseResult(
        phase="cross_language_bridge_recovery", build_id=ctx.build_id, data=None,
        stats=bridge_stats,
    ))

    # embedding_projection — chunk symbols and embed them
    embed_chunks = chunk_symbols(conn, ctx.target)
    embed_written = 0
    embed_warnings: list[str] = []
    try:
        embedder = Embedder()
        texts = [c.content for c in embed_chunks]
        if texts:
            vectors = embedder.embed_batch(texts)
            for chunk, vec in zip(embed_chunks, vectors):
                conn.execute(
                    "MERGE (c:Chunk {id: $id}) "
                    "SET c.owner_usr=$usr, c.chunk_kind=$kind, "
                    "c.content=$content, c.embedding=$emb",
                    {
                        "id": chunk.chunk_id,
                        "usr": chunk.owner_usr,
                        "kind": chunk.chunk_kind,
                        "content": chunk.content,
                        "emb": vec,
                    },
                )
                sid = f"{ctx.target}:{chunk.owner_usr}"
                conn.execute(
                    "MATCH (s:Symbol {id: $sid}), (c:Chunk {id: $cid}) "
                    "MERGE (s)-[:ContainsChunk]->(c)",
                    {"sid": sid, "cid": chunk.chunk_id},
                )
                embed_written += 1
    except EmbeddingError as e:
        embed_warnings.append(f"Ollama unavailable: {e}")

    results.append(PhaseResult(
        phase="embedding_projection", build_id=ctx.build_id, data=None,
        stats={"chunks": len(embed_chunks), "embedded": embed_written},
        warnings=embed_warnings,
    ))

    # call_graph_derivation — persist Calls + References + structural edges
    calls_written = 0
    refs_written = 0
    struct_written = 0
    if is_result is not None:
        calls_written = upsert_calls(
            conn, is_result.relations, ctx.target,
            source="indexstore", build_id=ctx.build_id,
        )
        refs_written = upsert_references(
            conn, is_result.relations, ctx.target, source="indexstore",
        )
        struct_written = upsert_indexstore_rels(
            conn, is_result.relations, ctx.target,
            source="indexstore", build_id=ctx.build_id,
        )
    results.append(PhaseResult(
        phase="call_graph_derivation", build_id=ctx.build_id, data=None,
        stats={"calls_written": calls_written, "references_written": refs_written,
               "structural_written": struct_written},
    ))

    # architecture_derivation — Module DependsOn edges + cycle detection
    arch_stats = run_architecture_derivation(conn, ctx.target, ctx.build_id)
    results.append(PhaseResult(
        phase="architecture_derivation", build_id=ctx.build_id, data=None,
        stats=arch_stats,
    ))

    # swiftui_derivation — ViewTree + NavigationFlow edges (placeholder heuristic)
    swiftui_stats = run_swiftui_derivation(conn, ctx.target, ctx.build_id)
    results.append(PhaseResult(
        phase="swiftui_derivation", build_id=ctx.build_id, data=None,
        stats=swiftui_stats,
    ))
    try:
        return results
    finally:
        conn.close()
