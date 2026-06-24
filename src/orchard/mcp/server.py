"""Orchard MCP server entry point.

Run as:
    orchard-mcp          # via the installed console_scripts entry point
    uv run orchard-mcp   # during development
"""

from __future__ import annotations

from mcp.server import FastMCP

from orchard.mcp.tools import register_tools


def main() -> None:
    """Create the FastMCP server, register all tools, and serve over stdio.

    The database path defaults to ``$ORCHARD_DB_PATH`` or ``~/.orchard/graph.db``.
    Override by setting the environment variable::

        ORCHARD_DB_PATH=/custom/path/graph.db orchard-mcp
    """
    server = FastMCP("orchard")
    register_tools(server)
    server.run(transport="stdio")
