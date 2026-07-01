"""Shared graph query helpers — avoids duplicating common Ladybug patterns.

Inspired by SourceKit-LSP's ``CheckedIndex`` wrapper around IndexStoreDB.
"""

from __future__ import annotations

import re

from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for, GraphFreshness
from orchard.handlers.base import reason_to_confidence
from orchard.derive.objc_semantics import classify_objc_message
from orchard.query.annotations import annotate_symbol_source_scope, execution_boundary_for

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
        sym = g.symbol("s:myFunc")
        owner = g.owner_of("s:myFunc")
        callers = g.callers_of("s:myFunc")
    """

    def __init__(self, conn):
        self._conn = conn
        self._container_names_cache: dict[str, list[str]] = {}
        self._callees_cache: dict[str, list[dict]] = {}
        self._callers_cache: dict[str, list[dict]] = {}
        self._workspace_root_cache: str | None = None
        self._target_action_cache: list[dict] | None = None

    # ---- Symbol resolution ------------------------------------------------

    def symbol(self, usr: str) -> dict | None:
        """Return a dict of Symbol fields for *usr*, or None."""
        tk = make_symbol_id(usr)
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
    def _filter_inferred(rows: list[tuple], include_inferred: bool) -> list[tuple]:
        """Filter CALLS edges by ``reason`` provenance.

        IndexStore tags every CALLS edge with a ``reason`` column:
          - ``source_direct`` — observed at a source-level call-site (high signal)
          - ``indexstore_relation_only`` — compiler type-inference edge (lower
            signal; e.g. protocol-default dispatch, override chains)
          - ``NULL`` — edge from symbolgraph (no reason column); semantically
            equivalent to source-level (explicitly declared).

        When *include_inferred* is False (the default):
          1. Exclude ``indexstore_relation_only`` edges.
          2. If ``source_direct`` edges exist for a given pair, prefer them
             over NULL-reason duplicates (dedup).

        When *include_inferred* is True the original behaviour is preserved:
          prefer ``source_direct`` when any exist, otherwise return all rows.
        """
        if include_inferred:
            if any((row[8] or "") == "source_direct" for row in rows):
                return [row for row in rows if (row[8] or "") == "source_direct"]
            return rows
        # Default: exclude compiler-inferred edges
        filtered = [row for row in rows if (row[8] or "") != "indexstore_relation_only"]
        # When source_direct exists, prefer it over NULL-reason duplicates
        # (e.g. an IndexStore source_direct edge and a symbolgraph edge for
        # the same (caller, callee) pair — keep only the source_direct one).
        if any((row[8] or "") == "source_direct" for row in filtered):
            return [row for row in filtered if (row[8] or "") == "source_direct"]
        return filtered

    def callers_of(self, usr: str,
                   relation_types: list[str] | None = None,
                   include_inferred: bool = False) -> list[dict]:
        """Return callers of *usr*.

        By default only source-level call evidence (``reason = 'source_direct'``)
        is returned.  Set *include_inferred* to True to see compiler-inferred
        edges (``indexstore_relation_only``) as well.
        """
        if relation_types is None:
            relation_types = ["Calls"]
        rel_pipe = "|".join(relation_types)
        sym_id = make_symbol_id(usr)
        rows = self._conn.execute(
            f"MATCH (caller:Symbol)-[r:{rel_pipe}]->(target:Symbol {{id: $id}}) "
            "RETURN DISTINCT caller.usr, caller.name, caller.module, "
            "caller.kind, caller.language, caller.file_path, r.reason",
            {"id": sym_id},
        ).get_all()
        # Pad rows to 9-element tuples to match _filter_inferred signature
        # (usr, name, module, kind, language, file_path, reason) → 9-tuple
        padded_rows = [(r[0], r[1], r[2], r[3], r[4], r[5], None, None, r[6]) for r in rows]
        preferred_rows = self._filter_inferred(padded_rows, include_inferred)
        callers: dict[str, dict] = {}
        for r in preferred_rows:
            reason_val = r[8] or "indexstore_relation_only"
            entry = callers.setdefault(
                r[0],
                annotate_symbol_source_scope(
                    {
                    "usr": r[0], "name": r[1], "module": r[2],
                    "kind": r[3], "language": r[4],
                    "file_path": r[5] or "",
                    "line": None,
                    "col": None,
                    "reason": reason_val,
                    "confidence": reason_to_confidence(reason_val),
                    "provenance": r[8] or "symbolgraph",
                    "owner": self.owner_of(r[0]),
                    },
                    self.workspace_root(),
                ),
            )
            boundary = execution_boundary_for(entry)
            if boundary:
                entry["execution_boundary"] = boundary
                entry["call_style"] = "async_or_callback_boundary"
            else:
                entry["call_style"] = "synchronous_call"
        return list(callers.values())

    def callees_of(self, usr: str,
                   relation_types: list[str] | None = None,
                   include_inferred: bool = False,
                   include_notification_bridges: bool = False) -> list[dict]:
        """Return callees of *usr*.

        By default only source-level call evidence (``reason = 'source_direct'``)
        is returned.  Set *include_inferred* to True to see compiler-inferred
        edges (``indexstore_relation_only``) as well.

        When *include_notification_bridges* is True, callees with
        ``semantic_role == "notification_observer"`` are annotated with
        ``notification_bridges`` — the matching Observes edges showing
        which notification, selector, and callback each observer is wired to.
        """
        if relation_types is None:
            relation_types = ["Calls"]
        cache_key = f"{usr}:{include_inferred}"
        if cache_key in self._callees_cache and not include_notification_bridges:
            return self._callees_cache[cache_key]
        rel_pipe = "|".join(relation_types)
        sym_id = make_symbol_id(usr)
        rows = self._conn.execute(
            f"MATCH (src:Symbol {{id: $id}})-[r:{rel_pipe}]->(callee:Symbol) "
            "RETURN DISTINCT callee.usr, callee.name, callee.module, "
            "callee.kind, callee.language, callee.file_path, r.reason",
            {"id": sym_id},
        ).get_all()
        preferred_rows = self._filter_inferred(
            [(r[0], r[1], r[2], r[3], r[4], r[5] or "", None, None, r[6]) for r in rows],
            include_inferred,
        )
        callees: dict[str, dict] = {}
        has_notification_observer = False
        has_target_action = False
        for r in preferred_rows:
            reason_val = r[8] or "indexstore_relation_only"
            lang = r[4] or ""
            entry = annotate_symbol_source_scope({
                "usr": r[0], "name": r[1], "module": r[2],
                "kind": r[3], "language": lang,
                "file_path": r[5] or "",
                "reason": reason_val,
                "confidence": reason_to_confidence(reason_val),
                "provenance": r[8] or "symbolgraph",
            }, self.workspace_root())
            if lang == "objc":
                role = classify_objc_message(r[1])
                entry["semantic_role"] = role
                if role == "notification_observer":
                    has_notification_observer = True
                if role == "target_action":
                    has_target_action = True
            boundary = execution_boundary_for(entry)
            if boundary:
                entry["execution_boundary"] = boundary
                entry["call_style"] = "async_or_callback_boundary"
            else:
                entry["call_style"] = "synchronous_call"
            callees.setdefault(r[0], entry)
        result = list(callees.values())

        # Enrich notification_observer callees with bridges from Observes edges.
        if include_notification_bridges and has_notification_observer:
            obs_rows = self._conn.execute(
                "MATCH (n:Notification)-[ob:Observes]->(cb:Symbol) "
                "WHERE ob.observer_usr = $usr "
                "RETURN n.name, ob.selector, cb.usr, cb.name, cb.module",
                {"usr": usr},
            ).get_all()
            if obs_rows:
                for r in obs_rows:
                    bridge = {
                        "notification_name": r[0],
                        "selector": r[1] or "",
                        "callback": {
                            "usr": r[2], "name": r[3], "module": r[4] or "",
                        },
                    }
                    # Attach to every notification_observer callee entry.
                    for entry in result:
                        if entry.get("semantic_role") == "notification_observer":
                            entry.setdefault("notification_bridges", []).append(bridge)

        if has_target_action:
            bridges = self.target_action_bridges_for_registrar(usr)
            if bridges:
                for entry in result:
                    if entry.get("semantic_role") == "target_action":
                        entry["target_action_bridges"] = bridges

        # Don't cache when bridges are included (bridge data varies by observer).
        if not include_notification_bridges:
            self._callees_cache[cache_key] = result
        return result

    def callees_of_depth(self, usr: str,
                         depth: int = 3,
                         relation_types: list[str] | None = None,
                         include_inferred: bool = False) -> list[dict]:
        """Return callees of *usr* up to *depth* hops via iterative BFS."""
        if relation_types is None:
            relation_types = ["Calls"]
        seen: set[str] = {usr}
        frontier: set[str] = {usr}
        results: list[dict] = []
        for d in range(1, depth + 1):
            next_frontier: set[str] = set()
            for f_usr in frontier:
                for c in self.callees_of(f_usr, relation_types,
                                         include_inferred=include_inferred):
                    if c["usr"] not in seen:
                        seen.add(c["usr"])
                        results.append({**c, "depth": d})
                    next_frontier.add(c["usr"])
            if not next_frontier:
                break
            frontier = next_frontier
        return results

    def callers_of_depth(self, usr: str,
                         depth: int = 3,
                         relation_types: list[str] | None = None,
                         include_inferred: bool = False) -> list[dict]:
        """Return callers of *usr* up to *depth* hops via iterative BFS (reverse)."""
        if relation_types is None:
            relation_types = ["Calls"]
        seen: set[str] = {usr}
        frontier: set[str] = {usr}
        results: list[dict] = []
        for d in range(1, depth + 1):
            next_frontier: set[str] = set()
            for f_usr in frontier:
                for c in self.callers_of(f_usr, relation_types,
                                         include_inferred=include_inferred):
                    if c["usr"] not in seen:
                        seen.add(c["usr"])
                        results.append({**c, "depth": d})
                    next_frontier.add(c["usr"])
            if not next_frontier:
                break
            frontier = next_frontier
        return results

    # ---- Class methods (Contains edge traversal) ---------------------------

    def methods_of(self, usr: str) -> list[dict]:
        """Return all method symbols contained by the given class/struct/enum/protocol.

        Traverses ``Contains`` edges from the parent symbol to child symbols
        where ``child.kind = 'method'``.  Returns a list of dicts with keys
        ``usr``, ``name``, ``kind``, ``language``.
        """
        sym_id = make_symbol_id(usr)
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

    def workspace_root(self) -> str:
        """Return the latest indexed workspace root, or the process cwd."""
        if self._workspace_root_cache is not None:
            return self._workspace_root_cache
        rows = self._conn.execute(
            "MATCH (b:BuildSnapshot) "
            "RETURN b.workspace_root ORDER BY b.created_at DESC LIMIT 1"
        ).get_all()
        self._workspace_root_cache = rows[0][0] if rows and rows[0][0] else ""
        return self._workspace_root_cache

    def _target_action_entries(self) -> list[dict]:
        """Return derived target-action entries for the current graph."""
        if self._target_action_cache is not None:
            return self._target_action_cache
        from orchard.derive.notification_graph import build_notification_graph

        graph = build_notification_graph(
            self._conn,
            source_root=self.workspace_root() or "",
        )
        self._target_action_cache = graph.get("target_actions", [])
        return self._target_action_cache

    def target_action_bridges_for_registrar(self, observer_usr: str) -> list[dict]:
        """Return target-action bridge details for a registrar method."""
        bridges: list[dict] = []
        for entry in self._target_action_entries():
            if entry.get("usr") != observer_usr:
                continue
            callback = entry.get("callback")
            bridge = {
                "line": entry.get("line"),
                "selector": entry.get("selector"),
                "control_event": entry.get("control_event"),
                "callback": None if not callback else {
                    "usr": callback.get("usr"),
                    "name": callback.get("name"),
                    "module": callback.get("module") or "",
                },
            }
            bridges.append(bridge)
        return bridges

    def target_action_bindings_for_callback(self, callback_usr: str) -> list[dict]:
        """Return target-action binding summaries keyed by callback USR."""
        bindings: list[dict] = []
        for entry in self._target_action_entries():
            callback = entry.get("callback") or {}
            if callback.get("usr") != callback_usr:
                continue
            bindings.append({
                "usr": entry.get("usr"),
                "name": entry.get("name"),
                "file_path": entry.get("file_path"),
                "module": entry.get("module") or "",
                "line": entry.get("line"),
                "selector": entry.get("selector"),
                "control_event": entry.get("control_event"),
                "callback_name": callback.get("name"),
            })
        return bindings

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

    def primary_definition_usr(self, usr: str) -> str | None:
        """Return the deterministic symbol ID for *usr*.

        Returns None if the symbol does not exist.
        """
        from orchard.normalize.identity import make_symbol_id
        sym_id = make_symbol_id(usr)
        rows = self._conn.execute(
            "MATCH (s:Symbol {id: $id}) RETURN s.id LIMIT 1",
            {"id": sym_id},
        ).get_all()
        return rows[0][0] if rows else None

    # ---- Freshness --------------------------------------------------------

    def freshness(self, build_id: str = "") -> tuple[GraphFreshness, str]:
        """Return freshness metadata for a build snapshot."""
        return freshness_for(self._conn, build_id or "", {})
