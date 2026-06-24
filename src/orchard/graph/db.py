"""
Ladybug database connection helpers for the Orchard Apple Semantic Graph.

Ladybug uses a two-object model:
  - ladybug.Database(path)   — opens/creates the on-disk database
  - ladybug.Connection(db)   — creates a connection for executing queries

get_connection() wraps both steps and returns a Connection that also holds a
reference to the Database so neither object is garbage-collected early.
"""

from __future__ import annotations

import ladybug

from orchard.graph.schema import SCHEMA_STATEMENTS


class _ConnectionWithDB:
    """Thin wrapper that keeps the Database alive alongside its Connection."""

    def __init__(self, db_path: str) -> None:
        self._db = ladybug.Database(db_path)
        self._conn = ladybug.Connection(self._db)

    # Delegate all attribute access to the underlying Connection so callers
    # can call .execute(), .close(), etc. directly.
    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        self._conn.close()


def get_connection(db_path: str) -> _ConnectionWithDB:
    """Open (or create) a Ladybug database at *db_path* and return a connection.

    Parameters
    ----------
    db_path:
        Filesystem path for the Ladybug database directory.

    Returns
    -------
    _ConnectionWithDB
        A connection object whose lifetime keeps the underlying Database alive.
    """
    return _ConnectionWithDB(db_path)


def init_schema(conn) -> None:
    """Run all CREATE NODE/REL TABLE DDL statements against *conn*.

    All statements use ``IF NOT EXISTS`` so this function is idempotent and
    safe to call on an existing database.

    Parameters
    ----------
    conn:
        An open Ladybug connection (as returned by :func:`get_connection`).
    """
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
