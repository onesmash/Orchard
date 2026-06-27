"""Shared graph query helpers — avoids duplicating common Ladybug patterns.

Inspired by SourceKit-LSP's ``CheckedIndex`` wrapper around IndexStoreDB.
"""

from __future__ import annotations

import re

from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for, GraphFreshness

# ---------------------------------------------------------------------------
# Framework callback detection
# ---------------------------------------------------------------------------

# Anchored regex patterns matching known Apple framework callback selectors.
# Each pattern is tested with re.match() so it is implicitly anchored at ^.
_FRAMEWORK_CALLBACK_PATTERNS: list[str] = [
    # UIApplicationDelegate
    r"^(application|userNotificationCenter):",
    # UISceneDelegate
    r"^(scene|windowScene):",
    # UIViewController lifecycle (allow optional trailing colon for ObjC selectors)
    r"^(viewDidLoad|viewWillAppear|viewDidAppear|viewWillDisappear|"
    r"viewDidDisappear|viewWillLayoutSubviews|viewDidLayoutSubviews|"
    r"loadView|awakeFromNib|prepareForSegue:sender:|didReceiveMemoryWarning):?$",
    # UITableViewDataSource
    r"^(tableView:numberOfRows|tableView:cellForRow|tableView:numberOfSections|"
    r"numberOfSectionsIn)",
    # UICollectionViewDataSource
    r"^(collectionView:numberOfItems|collectionView:cellForItem|"
    r"numberOfSectionsIn)",
]

_compiled_patterns: list[re.Pattern] = [re.compile(p) for p in _FRAMEWORK_CALLBACK_PATTERNS]


def is_framework_callback(name: str) -> bool:
    """Return True if *name* matches a known Apple framework callback selector.

    Uses anchored regex patterns to avoid false positives (e.g. ``^application:``
    will NOT match ``configureApplication:``).
    """
    return any(p.match(name) for p in _compiled_patterns)


class GraphLookup:
    """Wraps a Ladybug connection with common query methods.

    Each handler no longer needs to duplicate ``make_symbol_id`` +
    ``MATCH`` + ``freshness_for`` boilerplate.  Usage::

        g = GraphLookup(conn)
        sym = g.symbol("s:myFunc", "MyTarget")
        owner = g.owner_of("s:myFunc")
        callers = g.callers_of("s:myFunc", "MyTarget")
    """

    def __init__(self, conn):
        self._conn = conn
        self._container_names_cache: dict[str, list[str]] = {}

    # ---- Symbol resolution ------------------------------------------------

    def symbol(self, usr: str, target_id: str = "") -> dict | None:
        """Return a dict of Symbol fields for *usr*, or None."""
        tk = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            "MATCH (s:Symbol {id: $id}) "
            "RETURN s.usr, s.name, s.kind, s.language, s.module "
            "LIMIT 1",
            {"id": tk},
        ).get_all()
        if not rows:
            return None
        return {
            "usr": rows[0][0], "name": rows[0][1], "kind": rows[0][2],
            "language": rows[0][3], "module": rows[0][4],
        }

    # ---- Owner (SourceKit-LSP parent()) -----------------------------------

    def owner_of(self, usr: str) -> dict | None:
        """Walk Contains edges up to find the owning class/struct/extension.

        Results are cached per USR for the lifetime of this GraphLookup.
        Extension symbols resolve to the extended type via the Extends edge.
        """
        rows = self._conn.execute(
            "MATCH (s:Symbol {usr: $usr})<-[:Contains]-(owner:Symbol) "
            "WHERE owner.kind IN ['class','struct','enum','protocol','extension'] "
            "RETURN owner.usr, owner.name, owner.kind, owner.module LIMIT 1",
            {"usr": usr},
        ).get_all()
        if not rows:
            return None
        owner_usr, name, kind, module = rows[0]
        # If the owner is an extension, resolve to the extended type
        if kind == "extension":
            ext_rows = self._conn.execute(
                "MATCH (e:Symbol {usr: $usr})-[:Extends]->(ext:Symbol) "
                "RETURN ext.usr, ext.name, ext.kind, ext.module LIMIT 1",
                {"usr": owner_usr},
            ).get_all()
            if ext_rows:
                owner_usr, name, kind, module = ext_rows[0]
        result = {"usr": owner_usr, "name": name, "kind": kind, "module": module}
        self._container_names_cache[usr] = [result["name"]]
        return result

    # ---- Callers / callees ------------------------------------------------

    @staticmethod
    def _prefer_source_direct(rows: list[tuple]) -> list[tuple]:
        """Prefer source-level call evidence when any is available."""
        if any((row[8] or "") == "source_direct" for row in rows):
            return [row for row in rows if (row[8] or "") == "source_direct"]
        return rows

    def callers_of(self, usr: str, target_id: str = "",
                   relation_types: list[str] | None = None) -> list[dict]:
        """Return callers of *usr*, preferring source-level call evidence."""
        if relation_types is None:
            relation_types = ["Calls"]
        rel_pipe = "|".join(relation_types)
        sym_id = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            f"MATCH (caller:Symbol)-[r:{rel_pipe}]->(target:Symbol {{id: $id}}) "
            "OPTIONAL MATCH (f:File)-[:ContainsOccurrence]->(o:Occurrence {usr: caller.usr}) "
            "WHERE o.role = 'definition' "
            "RETURN DISTINCT caller.usr, caller.name, caller.module, "
            "caller.kind, caller.language, caller.file_path, o.line, o.col, r.reason",
            {"id": sym_id},
        ).get_all()
        preferred_rows = self._prefer_source_direct(rows)
        callers: dict[str, dict] = {}
        for r in preferred_rows:
            callers.setdefault(
                r[0],
                {
                    "usr": r[0], "name": r[1], "module": r[2],
                    "kind": r[3], "language": r[4],
                    "file_path": r[5] or "",
                    "line": r[6],
                    "col": r[7],
                    "reason": r[8] or "indexstore_relation_only",
                    "owner": self.owner_of(r[0]),
                },
            )
        return list(callers.values())

    def callees_of(self, usr: str, target_id: str = "",
                   relation_types: list[str] | None = None) -> list[dict]:
        """Return callees of *usr*, preferring source-level call evidence."""
        if relation_types is None:
            relation_types = ["Calls"]
        rel_pipe = "|".join(relation_types)
        sym_id = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            f"MATCH (src:Symbol {{id: $id}})-[r:{rel_pipe}]->(callee:Symbol) "
            "RETURN DISTINCT callee.usr, callee.name, callee.module, "
            "callee.kind, callee.language, r.reason",
            {"id": sym_id},
        ).get_all()
        preferred_rows = self._prefer_source_direct(
            [(r[0], r[1], r[2], r[3], r[4], "", None, None, r[5]) for r in rows]
        )
        callees: dict[str, dict] = {}
        for r in preferred_rows:
            callees.setdefault(
                r[0],
                {
                    "usr": r[0], "name": r[1], "module": r[2],
                    "kind": r[3], "language": r[4],
                    "reason": r[8] or "indexstore_relation_only",
                },
            )
        return list(callees.values())

    def callees_of_depth(self, usr: str, target_id: str = "",
                         depth: int = 3,
                         relation_types: list[str] | None = None) -> list[dict]:
        """Return callees of *usr* up to *depth* hops via iterative BFS."""
        if relation_types is None:
            relation_types = ["Calls"]
        seen: set[str] = {usr}
        frontier: set[str] = {usr}
        results: list[dict] = []
        for d in range(1, depth + 1):
            next_frontier: set[str] = set()
            for f_usr in frontier:
                for c in self.callees_of(f_usr, target_id, relation_types):
                    if c["usr"] not in seen:
                        seen.add(c["usr"])
                        results.append({**c, "depth": d})
                    next_frontier.add(c["usr"])
            if not next_frontier:
                break
            frontier = next_frontier
        return results

    def callers_of_depth(self, usr: str, target_id: str = "",
                         depth: int = 3,
                         relation_types: list[str] | None = None) -> list[dict]:
        """Return callers of *usr* up to *depth* hops via iterative BFS (reverse)."""
        if relation_types is None:
            relation_types = ["Calls"]
        seen: set[str] = {usr}
        frontier: set[str] = {usr}
        results: list[dict] = []
        for d in range(1, depth + 1):
            next_frontier: set[str] = set()
            for f_usr in frontier:
                for c in self.callers_of(f_usr, target_id, relation_types):
                    if c["usr"] not in seen:
                        seen.add(c["usr"])
                        results.append({**c, "depth": d})
                    next_frontier.add(c["usr"])
            if not next_frontier:
                break
            frontier = next_frontier
        return results

    # ---- Class methods (Contains edge traversal) ---------------------------

    def methods_of(self, usr: str, target_id: str = "") -> list[dict]:
        """Return all method symbols contained by the given class/struct/enum/protocol.

        Traverses ``Contains`` edges from the parent symbol to child symbols
        where ``child.kind = 'method'``.  Returns a list of dicts with keys
        ``usr``, ``name``, ``kind``, ``language``.
        """
        sym_id = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            "MATCH (parent:Symbol {id: $id})-[:Contains]->(child:Symbol) "
            "WHERE child.kind = 'method' "
            "RETURN DISTINCT child.usr, child.name, child.kind, child.language "
            "ORDER BY child.name",
            {"id": sym_id},
        ).get_all()
        return [
            {"usr": r[0], "name": r[1], "kind": r[2], "language": r[3]}
            for r in rows
        ]

    # ---- Module statistics --------------------------------------------------

    def module_stats(self) -> list[dict]:
        """Return per-module symbol counts grouped by kind.

        Returns a list of dicts with keys ``module``, ``kind``, ``count``,
        sorted by total symbols per module descending.
        """
        rows = self._conn.execute(
            "MATCH (s:Symbol) "
            "RETURN s.module AS module, s.kind AS kind, count(*) AS c "
            "ORDER BY module, c DESC"
        ).get_all()
        return [
            {"module": r[0], "kind": r[1], "count": r[2]}
            for r in rows
        ]

    # ---- Primary definition ------------------------------------------------

    def primary_definition_usr(self, usr: str, target_id: str = "") -> str | None:
        """Return the deterministic symbol ID for *usr* in *target_id*.

        The symbol ID is constructed as ``target||usr``, which is the primary
        key of the Symbol node table — inherently deterministic.  Returns
        None if the symbol does not exist in the given target.

        When *target_id* is empty, searches by USR alone and returns the
        first match sorted by (file_path, usr).
        """
        from orchard.normalize.identity import make_symbol_id
        if target_id:
            sym_id = make_symbol_id(target_id, usr)
            rows = self._conn.execute(
                "MATCH (s:Symbol {id: $id}) RETURN s.id LIMIT 1",
                {"id": sym_id},
            ).get_all()
            return rows[0][0] if rows else None
        # Fallback: search by USR, deterministic sort
        rows = self._conn.execute(
            "MATCH (s:Symbol) WHERE s.usr = $usr "
            "RETURN s.id ORDER BY s.file_path, s.usr LIMIT 1",
            {"usr": usr},
        ).get_all()
        return rows[0][0] if rows else None

    # ---- Freshness --------------------------------------------------------

    def freshness(self, build_id: str = "") -> tuple[GraphFreshness, str]:
        """Return freshness metadata for a build snapshot."""
        return freshness_for(self._conn, build_id or "", {})
