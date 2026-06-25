"""Shared graph query helpers — avoids duplicating common Ladybug patterns.

Inspired by SourceKit-LSP's ``CheckedIndex`` wrapper around IndexStoreDB.
"""

from __future__ import annotations

from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for, GraphFreshness


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

    def callers_of(self, usr: str, target_id: str = "") -> list[dict]:
        """Return all distinct callers of *usr* (with owner info)."""
        sym_id = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            "MATCH (caller:Symbol)-[:Calls]->(target:Symbol {id: $id}) "
            "RETURN DISTINCT caller.usr, caller.name, caller.module, "
            "caller.kind, caller.language",
            {"id": sym_id},
        ).get_all()
        return [
            {
                "usr": r[0], "name": r[1], "module": r[2],
                "kind": r[3], "language": r[4],
                "owner": self.owner_of(r[0]),
            }
            for r in rows
        ]

    def callees_of(self, usr: str, target_id: str = "") -> list[dict]:
        """Return all distinct callees of *usr*."""
        sym_id = make_symbol_id(target_id, usr)
        rows = self._conn.execute(
            "MATCH (src:Symbol {id: $id})-[:Calls]->(callee:Symbol) "
            "RETURN DISTINCT callee.usr, callee.name, callee.module, "
            "callee.kind, callee.language",
            {"id": sym_id},
        ).get_all()
        return [
            {"usr": r[0], "name": r[1], "module": r[2],
             "kind": r[3], "language": r[4]}
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
