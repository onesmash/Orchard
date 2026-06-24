"""Tool registration for the Orchard MCP server."""

from __future__ import annotations

import os

from mcp.server import FastMCP

from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.bridges import BridgesRequest, get_cross_language_bridges
from orchard.mcp.handlers.callees import CalleeRequest, find_callees
from orchard.mcp.handlers.callers import CallerRequest, find_callers
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis
from orchard.mcp.handlers.symbol_context import SymbolContextRequest, get_symbol_context
from orchard.mcp.handlers.type_hierarchy import TypeHierarchyRequest, get_type_hierarchy

DEFAULT_DB = os.environ.get("ORCHARD_DB_PATH", os.path.expanduser("~/.orchard/graph.db"))

# Module-level connection — keeps the underlying ladybug.Database alive.
_conn = None


def register_tools(server: FastMCP, db_path: str = DEFAULT_DB) -> None:
    """Register all MCP tools on *server* and open a DB connection at *db_path*."""
    global _conn
    _conn = get_connection(db_path)
    init_schema(_conn)

    @server.tool()
    def get_symbol_context_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
    ) -> dict:
        """Retrieve symbol context (name, kind, signature, etc.) from the semantic graph."""
        req = SymbolContextRequest(
            usr=usr,
            target_id=target_id or None,
            build_id=build_id or None,
        )
        return get_symbol_context(_conn, req).__dict__

    @server.tool()
    def find_callers_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
    ) -> dict:
        """Find all symbols that call the given symbol (upstream callers)."""
        req = CallerRequest(
            usr=usr,
            target_id=target_id or None,
            build_id=build_id or None,
        )
        return find_callers(_conn, req).__dict__

    @server.tool()
    def find_callees_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
    ) -> dict:
        """Find all symbols called by the given symbol (downstream callees)."""
        req = CalleeRequest(
            usr=usr,
            target_id=target_id or None,
            build_id=build_id or None,
        )
        return find_callees(_conn, req).__dict__

    @server.tool()
    def get_type_hierarchy_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
    ) -> dict:
        """Get the type hierarchy (parents, protocols, children) for a symbol."""
        req = TypeHierarchyRequest(
            usr=usr,
            target_id=target_id or None,
            build_id=build_id or None,
        )
        return get_type_hierarchy(_conn, req).__dict__

    @server.tool()
    def get_cross_language_bridges_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
    ) -> dict:
        """Return cross-language BridgesTo edges for a symbol."""
        req = BridgesRequest(
            usr=usr, target_id=target_id or None, build_id=build_id or None,
        )
        return get_cross_language_bridges(_conn, req).__dict__

    @server.tool()
    def impact_analysis_tool(
        usr: str,
        target_id: str = "",
        build_id: str = "",
        max_depth: int = 5,
    ) -> dict:
        """Traverse call graph and return dependents by depth with risk score."""
        req = ImpactRequest(
            usr=usr, target_id=target_id or None, build_id=build_id or None,
            max_depth=max_depth,
        )
        return impact_analysis(_conn, req).__dict__
