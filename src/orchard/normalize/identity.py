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
    """Upsert Symbol nodes via COPY FROM CSV — fast bulk import."""
    import csv, tempfile, os
    t0 = time.monotonic()
    # Pre-fetch existing IDs so we only COPY new symbols (idempotent without
    # IGNORE_ERRORS, which would mask real schema violations).
    id_rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) RETURN s.id",
        {"tid": target_id},
    ).get_all()
    existing = {r[0] for r in id_rows}
    existing_rows = {make_symbol_id(target_id, s.usr): s for s in symbols if make_symbol_id(target_id, s.usr) in existing}
    for sym_id, s in existing_rows.items():
        conn.execute(
            "MATCH (s:Symbol {id: $id}) "
            "SET s.precise_id=$precise_id, s.name=$name, s.swift_display_name=$swift_display_name, "
            "s.language=$language, s.kind=$kind, s.module=$module, s.file_path=$file_path, "
            "s.signature=$signature, s.container_usr=$container_usr, s.access_level=$access_level",
            {
                "id": sym_id,
                "precise_id": s.precise_id or "",
                "name": s.name,
                "swift_display_name": s.swift_display_name or "",
                "language": s.language,
                "kind": s.kind,
                "module": s.module,
                "file_path": s.file_path or "",
                "signature": s.signature or "",
                "container_usr": s.container_usr or "",
                "access_level": s.access_level,
            },
        )
    new = [s for s in symbols if make_symbol_id(target_id, s.usr) not in existing]
    if not new:
        _perf_probes.setdefault("upsert_symbols_s", 0.0)
        _perf_probes.setdefault("upsert_symbols_n", 0)
        return len(existing_rows)
    csv_path = os.path.join(tempfile.mkdtemp(), "symbols.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for s in new:
            w.writerow([
                make_symbol_id(target_id, s.usr),
                s.usr, s.precise_id or "", s.name, s.swift_display_name or "",
                s.language, s.kind,
                s.module, target_id, s.file_path or "", s.signature or "",
                s.container_usr or "", s.access_level, "swift_symbolgraph", "false",
            ])
    if _progress:
        sys.stdout.write(f"  csv {os.path.getsize(csv_path)/1024/1024:.0f}MB, importing...")
        sys.stdout.flush()
    conn.execute(f"COPY Symbol FROM '{csv_path}' (HEADER=false, DELIM=',')")
    os.unlink(csv_path)
    conn.execute("CHECKPOINT")
    t = round(time.monotonic() - t0, 3)
    _perf_probes.setdefault("upsert_symbols_s", t)
    _perf_probes.setdefault("upsert_symbols_n", len(symbols))
    return len(new) + len(existing_rows)


def prune_missing_symbols(conn, target_id: str, active_usrs: set[str]) -> int:
    """Delete target-scoped Symbol nodes whose USRs are absent from this build."""
    rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) RETURN s.usr",
        {"tid": target_id},
    ).get_all()
    stale_usrs = [r[0] for r in rows if r[0] not in active_usrs]
    for usr in stale_usrs:
        conn.execute(
            "MATCH (s:Symbol {target_id: $tid, usr: $usr}) DETACH DELETE s",
            {"tid": target_id, "usr": usr},
        )
    return len(stale_usrs)


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
    "extendedBy": "Extends",  # ObjC category: NSString extendedBy CalendarDateFromString
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
            # IndexStore roles describe the relationship FROM the related
            # symbol TO the occurrence symbol. E.g. baseOf: related IS the
            # base of the occurrence. So occurrence inherits from related.
            # Inherits(occurrence → related).
            # Empirically, this is the SWAPPED direction:
            #   UIViewController --inherits-> ZMClips... is wrong, so
            #   we write (t_id, s_id) = Inherits(related → occurrence).
            by_table.setdefault(table, []).append((t_id, s_id))
    import csv, tempfile, os
    count = 0
    for table, pairs in by_table.items():
        csv_path = os.path.join(tempfile.mkdtemp(), f"{table}.csv")
        with open(csv_path, "w", newline="") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_ALL)
            for s, t in pairs:
                w.writerow([s, t, source, "0.90", "indexstore"])
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

    When the underlying relation was observed from a source-level call-site
    occurrence, ``reason`` is stored as ``source_direct``. Otherwise the edge
    remains ``indexstore_relation_only`` so later query layers can distinguish
    compiler/index relations from source call evidence.

    Uses Ladybug's ``COPY FROM`` bulk importer — orders of magnitude faster
    than UNWIND + CREATE for large edge sets (596k edges import in seconds).
    """
    t0 = time.monotonic()
    called: dict[tuple[str, str], str] = {}
    for rel in relations:
        if rel.role != "calledBy":
            continue
        pair = (rel.to_usr, rel.from_usr)
        existing = called.get(pair)
        reason = "source_direct" if rel.occurrence_role == "call" else "indexstore_relation_only"
        if existing == "source_direct":
            continue
        called[pair] = reason
    if not called:
        _perf_probes["upsert_calls_s"] = 0
        _perf_probes["upsert_calls_n"] = 0
        return 0

    import csv, tempfile, os
    csv_path = os.path.join(tempfile.mkdtemp(), "calls.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for (to_u, fm_u), reason in called.items():
            w.writerow([
                make_symbol_id(target_id, to_u),
                make_symbol_id(target_id, fm_u),
                source, "1.0", "indexstore", build_id, reason,
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
    conn.execute(
        "MERGE (t:Target {id: $target_id}) "
        "SET t.name = $target_name, t.sdk = $sdk, t.configuration = $configuration, t.triple = $triple "
        "WITH t "
        "MATCH (b:BuildSnapshot {id: $build_id}) "
        "MERGE (b)-[:BuiltTarget]->(t)",
        {
            "target_id": ctx.target,
            "target_name": ctx.target,
            "sdk": ctx.sdk,
            "configuration": ctx.configuration,
            "triple": ctx.triple,
            "build_id": ctx.build_id,
        },
    )
