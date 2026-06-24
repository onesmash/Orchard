"""Orchard MCP server entry point.

Run as:
    orchard-mcp          # via the installed console_scripts entry point
    uv run orchard-mcp   # during development
"""

from __future__ import annotations

from mcp.server import FastMCP

from orchard.mcp.tools import register_tools


def main() -> None:
    """Create the FastMCP server, register all tools, and serve over stdio."""
    server = FastMCP("orchard")
    register_tools(server)
    server.run(transport="stdio")
