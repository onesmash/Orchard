"""cross_language_bridge_recovery phase.

Discovers ObjC ↔ Swift bridge candidates by matching symbols across
languages within the same target and writes BridgesTo edges.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchard.normalize.identity import make_symbol_id


@dataclass
class CrossLanguageName:
    """Dual-language symbol name for ObjC/Swift interop.

    Inspired by sourcekit-lsp's CrossLanguageName.
    """
    clang_name: str | None = None   # -[Class method:] / +[Class method:]
    swift_name: str | None = None   # Class.method(_:)
    definition_language: str = ""   # "swift" | "objc" | "c"

    @property
    def definition_name(self) -> str | None:
        """Return the name in the symbol's definition language."""
        if self.definition_language == "swift":
            return self.swift_name
        if self.definition_language in ("objc", "c", "cpp"):
            return self.clang_name
        return self.swift_name or self.clang_name


def run_bridge_recovery(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Find cross-language bridge candidates and write BridgesTo edges.

    Strategies (in priority order):
      1. Name match: same base name + different language → confidence 0.70.
      2. USR correlation (deferred to M4).

    Uses MERGE for idempotency — repeated runs with the same data produce
    no new edges. Counts reflect only *new* edges written in this call.

    Parameters
    ----------
    conn
        Open Ladybug connection.
    target_id
        The build target identifier.
    build_id
        The build snapshot identifier.

    Returns
    -------
    dict
        Counters: ``bridges_by_name``, ``total``.
    """
    # Count existing edges to report only *new* ones (delta-based idempotency).
    before = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    before_count = int(before[0][0]) if before else 0

    # Strategy 1: Name + kind match across languages.
    # Strategy 2: Same source directory → USR correlation (higher confidence).
    # Simple cross-language scan using only Symbol nodes (no File/Target edges
    # required).  Symbols must differ in language, both be swift or objc, and
    # belong to the same target.
    rows = conn.execute(
        "MATCH (a:Symbol), (b:Symbol) "
        "WHERE a.name = b.name AND a.kind = b.kind "
        "  AND a.language <> b.language AND a.language IN ['swift','objc'] "
        "  AND b.language IN ['swift','objc'] "
        "  AND a.target_id = $tid AND b.target_id = $tid "
        "RETURN a.usr, b.usr, a.file_path, b.file_path LIMIT 5000",
        {"tid": target_id},
    ).get_all()

    # Deduplicate pairs and determine confidence tier.
    # (usr_a, usr_b, kind, confidence)
    pairs: dict[tuple[str, str], tuple[str, float]] = {}
    for row in rows:
        usr_a, usr_b, fp_a, fp_b = row[0], row[1], row[2] or "", row[3] or ""
        pair_key = tuple(sorted([usr_a, usr_b]))
        # Strategy 2: same source directory → USR correlation (0.85)
        # Falls back to name_match (0.70) when file paths differ.
        from pathlib import PurePosixPath
        same_dir = bool(fp_a and fp_b and
                        PurePosixPath(fp_a).parent == PurePosixPath(fp_b).parent)
        kind = "usr_correlate" if same_dir else "name_match"
        conf = 0.85 if same_dir else 0.70
        pairs[pair_key] = (kind, conf)

    # Write bidirectional BridgesTo edges via MERGE.
    counts: dict[str, int] = {"bridges_by_name": 0, "bridges_by_usr": 0, "total": 0}

    for (usr_a, usr_b), (kind, conf) in pairs.items():
        if kind == "usr_correlate":
            counts["bridges_by_usr"] += 2  # bidirectional
        else:
            counts["bridges_by_name"] += 2
        counts["total"] += 2
        for src_usr, tgt_usr in [(usr_a, usr_b), (usr_b, usr_a)]:
            conn.execute(
                "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
                "MERGE (a)-[:BridgesTo {bridge_kind: $kind, provenance: $prov, "
                "confidence: $conf, build_id: $bid}]->(b)",
                {
                    "src": make_symbol_id(target_id, src_usr),
                    "dst": make_symbol_id(target_id, tgt_usr),
                    "kind": kind,
                    "prov": "derive/bridge",
                    "conf": conf,
                    "bid": build_id,
                },
            )

    # Report delta (new edges only) for idempotency.
    after = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    after_count = int(after[0][0]) if after else 0
    new_total = max(0, after_count - before_count)
    return {
        "bridges_by_name": counts["bridges_by_name"],
        "bridges_by_usr": counts["bridges_by_usr"],
        "total": new_total,
    }
