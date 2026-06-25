"""
Identity normalization for Orchard Apple Semantic Graph.

Provides target-scoped composite key generation and graph upsert helpers
for symbols, relationships, and build snapshots.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.ingest.indexstore import RelationRecord
from orchard.build.context import BuildContext

# Performance probes — module-level dict populated by the upsert functions.
# Keys: "upsert_symbols_s"/"_n", "upsert_calls_s"/"_n", "upsert_struct_s"/"_n".
_perf_probes: dict[str, float] = {}
_progress: bool = False


def get_perf_probes() -> dict[str, float]:
    return dict(_perf_probes)


def enable_progress() -> None:
    global _progress
    _progress = True


def make_symbol_id(target_id: str, usr: str) -> str:
    """Return a target-scoped composite symbol ID: '{target_id}:{usr}'."""
    return f"{target_id}:{usr}"


_SYMBOL_BATCH_SIZE = 2000
_EDGE_BATCH_SIZE = 200


def upsert_symbols(conn, symbols: list[SymbolRecord], target_id: str) -> int:
    """Upsert Symbol nodes into the graph for the given target.

    Uses UNWIND batching for large symbol lists — one Cypher query per
    ``_SYMBOL_BATCH_SIZE`` rows, avoiding per-symbol round-trips.
    """
    t0 = time.monotonic()
    count = 0
    for i in range(0, len(symbols), _SYMBOL_BATCH_SIZE):
        batch = symbols[i : i + _SYMBOL_BATCH_SIZE]
        rows = [
            {
                "id": make_symbol_id(target_id, s.usr),
                "usr": s.usr,
                "precise": s.precise_id or "",
                "name": s.name,
                "lang": s.language,
                "kind": s.kind,
                "mod": s.module,
                "file": s.file_path or "",
                "sig": s.signature or "",
                "container": s.container_usr or "",
                "access": s.access_level,
            }
            for s in batch
        ]
        conn.execute(
            "UNWIND $rows AS r "
            "MERGE (s:Symbol {id: r.id}) "
            "SET s.usr=r.usr, s.precise_id=r.precise, s.name=r.name, "
            "s.language=r.lang, s.kind=r.kind, s.module=r.mod, "
            "s.target_id=$tid, s.file_path=r.file, s.signature=r.sig, "
            "s.container_usr=r.container, s.access_level=r.access, "
            "s.origin='swift_symbolgraph', s.is_generated=false",
            {"rows": rows, "tid": target_id},
        )
        count += len(batch)
        if _progress:
            sys.stdout.write(f"\r  symbols: {count}/{len(symbols)} ({count*100//len(symbols)}%)")
            sys.stdout.flush()
    conn.execute("CHECKPOINT")
    t = round(time.monotonic() - t0, 3)
    _perf_probes.setdefault("upsert_symbols_s", t)
    _perf_probes.setdefault("upsert_symbols_n", count)
    return count


# Mapping from symbolgraph relationship kinds to Ladybug rel table names.
_REL_KIND_TO_TABLE: dict[str, str] = {
    "memberOf": "Declares",
    "conformsTo": "ConformsTo",
    "inheritsFrom": "Inherits",
    "overrides": "Implements",
}

# Mapping from IndexStore relation roles to Ladybug rel table names.
# Direction: from_usr is the subject (occurrence's symbol), to_usr is the
# related symbol. E.g. baseOf(from_usr=Derived, to_usr=Base) means Derived
# inherits from Base → Inherits(Derived → Base).
_INDEXSTORE_REL_TO_TABLE: dict[str, str] = {
    "baseOf": "Inherits",
    "overrideOf": "Implements",
    "childOf": "Contains",
    "containedBy": "Contains",
    # extendedBy is ObjC category extension, NOT subclassing — skip it.
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


def upsert_indexstore_rels(
    conn,
    rels: list[RelationRecord],
    target_id: str,
    source: str,
    build_id: str,
) -> int:
    """Upsert IndexStore structural relation edges via COPY FROM CSV.

    Maps IndexStore relation roles to Ladybug table names (see
    ``_INDEXSTORE_REL_TO_TABLE``).  Each table gets a separate CSV import.
    """
    t0 = time.monotonic()
    # Pre-fetch the set of existing Symbol IDs so we only write edges whose
    # both endpoints exist (COPY FROM rejects missing primary keys).
    id_rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) RETURN s.id",
        {"tid": target_id},
    ).get_all()
    existing_ids = {r[0] for r in id_rows}
    # Group by target table, filtering to valid pairs.
    by_table: dict[str, list[tuple[str, str]]] = {}
    for rel in rels:
        table = _INDEXSTORE_REL_TO_TABLE.get(rel.role)
        if table is None:
            continue
        s_id = make_symbol_id(target_id, rel.from_usr)
        t_id = make_symbol_id(target_id, rel.to_usr)
        if s_id in existing_ids and t_id in existing_ids:
            # Forward direction: the occurrence symbol (from_usr) is the
            # subject; the related symbol (to_usr) is the descriptor.
            #   baseOf(from=A, to=B): B is a base of A → Inherits(A → B).
            #   childOf(from=A, to=B): B is a child of A → A contains B → Contains(A → B).
            by_table.setdefault(table, []).append((s_id, t_id))
    import csv, tempfile, os
    count = 0
    for table, pairs in by_table.items():
        csv_path = os.path.join(tempfile.mkdtemp(), f"{table}.csv")
        with open(csv_path, "w", newline="") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_ALL)
            for s, t in pairs:
                w.writerow([s, t, source])
        if _progress:
            sys.stdout.write(f"  importing {len(pairs):,} {table} edges...")
            sys.stdout.flush()
        conn.execute(f"COPY {table} FROM '{csv_path}' (HEADER=false, DELIM=',')")
        os.unlink(csv_path)
        count += len(pairs)
    conn.execute("CHECKPOINT")
    t = round(time.monotonic() - t0, 3)
    _perf_probes["upsert_struct_s"] = t
    _perf_probes["upsert_struct_n"] = count
    return count


def upsert_calls(
    conn,
    relations: list[RelationRecord],
    target_id: str,
    source: str,
    build_id: str,
) -> int:
    """Upsert Calls edges via COPY FROM CSV.

    IndexStore role 'calledBy': ``from_usr`` is called by ``to_usr``, i.e.
    ``to_usr`` calls ``from_usr``. The CALLER is ``to_usr``, the CALLEE is
    ``from_usr``.  Edge written: ``Calls(caller=to_usr, callee=from_usr)``.

    Uses Ladybug's ``COPY FROM`` bulk importer — orders of magnitude faster
    than UNWIND + CREATE for large edge sets (596k edges import in seconds).
    """
    t0 = time.monotonic()
    called = [(r.to_usr, r.from_usr) for r in relations if r.role == "calledBy"]
    if not called:
        _perf_probes["upsert_calls_s"] = 0
        _perf_probes["upsert_calls_n"] = 0
        return 0

    import csv, tempfile, os
    csv_path = os.path.join(tempfile.mkdtemp(), "calls.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for to_u, fm_u in called:
            w.writerow([
                make_symbol_id(target_id, to_u),
                make_symbol_id(target_id, fm_u),
                source, "1.0", "indexstore", build_id,
            ])
    if _progress:
        sys.stdout.write(f"  csv {os.path.getsize(csv_path)/1024/1024:.0f}MB, importing...")
        sys.stdout.flush()
    conn.execute(
        f"COPY Calls FROM '{csv_path}' (HEADER=false, DELIM=',')"
    )
    os.unlink(csv_path)
    count = len(called)
    conn.execute("CHECKPOINT")
    t = round(time.monotonic() - t0, 3)
    _perf_probes["upsert_calls_s"] = t
    _perf_probes["upsert_calls_n"] = count
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
