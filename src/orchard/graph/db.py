"""
Ladybug database connection helpers for the Orchard Apple Semantic Graph.

Ladybug uses a two-object model:
  - ladybug.Database(path)   — opens/creates the on-disk database
  - ladybug.Connection(db)   — creates a connection for executing queries

get_connection() wraps both steps and returns a Connection that also holds a
reference to the Database so neither object is garbage-collected early.
"""

from __future__ import annotations

import os

import ladybug

from orchard.graph.schema import SCHEMA_STATEMENTS


class _ConnectionWithDB:
    """Thin wrapper that keeps the Database alive alongside its Connection."""

    def __init__(self, db_path: str) -> None:
        # Ladybug cannot open a database whose parent directory does not exist,
        # so ensure it is present (e.g. the default ~/.orchard/graph.db on a
        # fresh install). Existing directories are a no-op.
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
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
    safe to call on an existing database.  For databases created before new
    columns were added, :func:`migrate_schema` back-fills the missing columns
    via ``ALTER TABLE ... ADD ... IF NOT EXISTS``.

    Parameters
    ----------
    conn:
        An open Ladybug connection (as returned by :func:`get_connection`).
    """
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    migrate_schema(conn)


# Columns added after the initial schema.  Each entry is
# (table_name, column_name, column_type).  migrate_schema() adds any that are
# missing on existing databases via ALTER TABLE ... ADD IF NOT EXISTS.
_MIGRATION_COLUMNS: list[tuple[str, str, str]] = [
    ("Symbol", "swift_display_name", "STRING"),
    ("Calls", "reason", "STRING"),
    ("References", "reason", "STRING"),
    ("Contains", "confidence", "DOUBLE"),
    ("Contains", "reason", "STRING"),
    ("Extends", "confidence", "DOUBLE"),
    ("Extends", "reason", "STRING"),
    ("Inherits", "confidence", "DOUBLE"),
    ("Inherits", "reason", "STRING"),
    ("Implements", "confidence", "DOUBLE"),
    ("Implements", "reason", "STRING"),
    ("ConformsTo", "confidence", "DOUBLE"),
    ("ConformsTo", "reason", "STRING"),
    ("BridgesTo", "reason", "STRING"),
    ("BridgesTo", "clang_name", "STRING"),
    ("BridgesTo", "swift_name", "STRING"),
    ("BridgesTo", "definition_language", "STRING"),
    ("ViewTree", "reason", "STRING"),
    ("NavigationFlow", "reason", "STRING"),
]


def migrate_schema(conn) -> None:
    """Back-fill columns added after the initial schema on existing databases.

    Uses ``ALTER TABLE ... ADD COLUMN``.  Already-existing columns and
    missing tables are silently ignored (table errors → CREATE handles them,
    duplicate columns → column already exists → no-op).
    """
    for table, col, col_type in _MIGRATION_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD {col} {col_type}"
            )
        except Exception as exc:
            msg = str(exc).lower()
            # Column already exists or table not yet created — safe to skip.
            if "already has property" in msg or "does not exist" in msg:
                continue
            raise
