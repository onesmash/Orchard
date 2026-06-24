"""
Identity normalization for Orchard Apple Semantic Graph.

Provides target-scoped composite key generation and graph upsert helpers
for symbols, relationships, and build snapshots.
"""

from __future__ import annotations

from datetime import datetime, timezone

from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.ingest.indexstore import RelationRecord
from orchard.build.context import BuildContext


def make_symbol_id(target_id: str, usr: str) -> str:
    """Return a target-scoped composite symbol ID: '{target_id}:{usr}'."""
    return f"{target_id}:{usr}"


def upsert_symbols(conn, symbols: list[SymbolRecord], target_id: str) -> int:
    """Upsert Symbol nodes into the graph for the given target.

    Parameters
    ----------
    conn:
        An open Ladybug connection.
    symbols:
        List of SymbolRecord objects to write.
    target_id:
        The build target identifier used to namespace symbol IDs.

    Returns
    -------
    int
        Number of symbols processed.
    """
    count = 0
    for sym in symbols:
        sid = make_symbol_id(target_id, sym.usr)
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.usr = $usr, s.precise_id = $precise_id, s.name = $name, "
            "s.language = $language, s.kind = $kind, s.module = $module, "
            "s.target_id = $target_id, s.file_path = $file_path, "
            "s.signature = $signature, s.container_usr = $container_usr, "
            "s.access_level = $access_level, s.origin = $origin, "
            "s.is_generated = false",
            {
                "id": sid,
                "usr": sym.usr,
                "precise_id": sym.precise_id or "",
                "name": sym.name,
                "language": sym.language,
                "kind": sym.kind,
                "module": sym.module,
                "target_id": target_id,
                "file_path": sym.file_path or "",
                "signature": sym.signature or "",
                "container_usr": sym.container_usr or "",
                "access_level": sym.access_level,
                "origin": "swift_symbolgraph",
            },
        )
        count += 1
    return count


# Mapping from symbolgraph relationship kinds to Ladybug rel table names.
_REL_KIND_TO_TABLE: dict[str, str] = {
    "memberOf": "Declares",
    "conformsTo": "ConformsTo",
    "inheritsFrom": "Inherits",
    "overrides": "Implements",
}


def upsert_symbol_rels(
    conn,
    rels: list[SymbolRelRecord],
    target_id: str,
    source: str,
) -> int:
    """Upsert symbol relationship edges into the graph.

    Only relationship kinds with a known table mapping are written; unknown
    kinds are silently skipped.

    Parameters
    ----------
    conn:
        An open Ladybug connection.
    rels:
        List of SymbolRelRecord objects to write.
    target_id:
        The build target identifier used to namespace symbol IDs.
    source:
        Provenance tag stored on each edge (e.g. a symbolgraph filename).

    Returns
    -------
    int
        Number of relationships written (skipped ones not counted).
    """
    count = 0
    for rel in rels:
        table = _REL_KIND_TO_TABLE.get(rel.rel_kind)
        if table is None:
            continue
        src_id = make_symbol_id(target_id, rel.source_usr)
        tgt_id = make_symbol_id(target_id, rel.target_usr)
        conn.execute(
            f"MATCH (a:Symbol {{id: $src}}), (b:Symbol {{id: $tgt}}) "
            f"MERGE (a)-[:{table} {{source: $source}}]->(b)",
            {"src": src_id, "tgt": tgt_id, "source": source},
        )
        count += 1
    return count


def upsert_calls(
    conn,
    relations: list[RelationRecord],
    target_id: str,
    source: str,
    build_id: str,
) -> int:
    """Upsert Calls edges from IndexStore relations.

    IndexStore role 'calledBy': ``from_usr`` is called by ``to_usr``, i.e.
    ``to_usr`` calls ``from_usr``. The CALLER is therefore ``to_usr`` and the
    CALLEE is ``from_usr``. Edge written: ``Calls(caller=to_usr, callee=from_usr)``.

    Roles other than ``calledBy`` are silently skipped. Only edges whose
    endpoints already exist as Symbol nodes are written (MATCH-then-MERGE);
    missing endpoints are silently dropped, consistent with ``upsert_symbol_rels``.
    """
    count = 0
    for rel in relations:
        if rel.role != "calledBy":
            continue
        caller_id = make_symbol_id(target_id, rel.to_usr)
        callee_id = make_symbol_id(target_id, rel.from_usr)
        conn.execute(
            "MATCH (caller:Symbol {id: $caller}), (callee:Symbol {id: $callee}) "
            "MERGE (caller)-[:Calls {source: $source, build_id: $build_id}]->(callee)",
            {"caller": caller_id, "callee": callee_id,
             "source": source, "build_id": build_id},
        )
        count += 1
    return count


def upsert_references(
    conn,
    relations: list[RelationRecord],
    target_id: str,
    source: str,
) -> int:
    """Upsert References edges from IndexStore relations.

    IndexStore role 'references': ``from_usr`` references ``to_usr``, so the
    edge is ``References(from_usr -> to_usr)``.

    Roles other than ``references`` are silently skipped. Only edges whose
    endpoints already exist as Symbol nodes are written (MATCH-then-MERGE).
    """
    count = 0
    for rel in relations:
        if rel.role != "references":
            continue
        src_id = make_symbol_id(target_id, rel.from_usr)
        tgt_id = make_symbol_id(target_id, rel.to_usr)
        conn.execute(
            "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $tgt}) "
            "MERGE (a)-[:References {source: $source}]->(b)",
            {"src": src_id, "tgt": tgt_id, "source": source},
        )
        count += 1
    return count


def upsert_build_snapshot(conn, ctx: BuildContext) -> None:
    """Upsert a BuildSnapshot node for the given build context.

    Parameters
    ----------
    conn:
        An open Ladybug connection.
    ctx:
        The BuildContext describing this build.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "MERGE (b:BuildSnapshot {id: $id}) "
        "SET b.build_system = $build_system, b.workspace_root = $workspace_root, "
        "b.derived_data_path = $derived_data_path, "
        "b.index_store_path = $index_store_path, "
        "b.toolchain_id = $toolchain_id, b.commit_sha = $commit_sha, "
        "b.build_config_hash = $build_config_hash, "
        "b.sdk = $sdk, b.configuration = $configuration, "
        "b.created_at = $created_at",
        {
            "id": ctx.build_id,
            "build_system": ctx.build_system,
            "workspace_root": ctx.workspace_root,
            "derived_data_path": ctx.derived_data_path or "",
            "index_store_path": ctx.index_store_path or "",
            "toolchain_id": ctx.toolchain_id,
            "commit_sha": ctx.commit_sha or "",
            "build_config_hash": ctx.build_config_hash,
            "sdk": ctx.sdk,
            "configuration": ctx.configuration,
            "created_at": created_at,
        },
    )
