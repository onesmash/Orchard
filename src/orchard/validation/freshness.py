"""
GraphFreshness tracking for build snapshots and per-occurrence freshness checks.

Provides freshness validation to determine if a build snapshot is current
relative to the requested toolchain and build configuration, plus per-file
freshness checks inspired by sourcekit-lsp's IndexOutOfDateChecker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class IndexCheckLevel(Enum):
    """Granularity of index freshness checks (inspired by sourcekit-lsp)."""
    DELETED_FILES = "deleted_files"
    MODIFIED_FILES = "modified_files"
    IN_MEMORY_MODIFIED_FILES = "in_memory_modified_files"

    @classmethod
    def default(cls) -> "IndexCheckLevel":
        return cls.MODIFIED_FILES


@dataclass
class SymbolLocation:
    """Minimal location for per-occurrence freshness checking."""
    path: str
    timestamp: float  # Unix timestamp of when this symbol was indexed


class IndexOutOfDateChecker:
    """Checks whether indexed symbol locations are still up-to-date.

    Caches file modification times for the lifetime of one request.
    Inspired by sourcekit-lsp's IndexOutOfDateChecker.
    """

    def __init__(self, check_level: IndexCheckLevel | None = None):
        self._check_level = check_level or IndexCheckLevel.default()
        self._mod_time_cache: dict[str, float | None] = {}
        self._file_exists_cache: dict[str, bool] = {}

    def is_up_to_date(self, location: SymbolLocation) -> bool:
        """Return True if the source file hasn't been modified since indexing."""
        if self._check_level == IndexCheckLevel.DELETED_FILES:
            return self._file_exists(location.path)
        source_mtime = self._modification_time(location.path)
        if source_mtime is None:
            return False  # file deleted
        return source_mtime <= location.timestamp

    def _file_exists(self, path: str) -> bool:
        if path not in self._file_exists_cache:
            self._file_exists_cache[path] = os.path.exists(path)
        return self._file_exists_cache[path]

    def _modification_time(self, path: str) -> float | None:
        if path not in self._mod_time_cache:
            try:
                self._mod_time_cache[path] = os.path.getmtime(path)
            except OSError:
                self._mod_time_cache[path] = None
        return self._mod_time_cache[path]


@dataclass
class GraphFreshness:
    """Metadata about the freshness state of a build snapshot.

    Attributes
    ----------
    build_id : str
        Unique identifier for the build snapshot.
    created_at : str
        ISO timestamp when the snapshot was created.
    commit_sha : str | None
        The commit SHA used in this build, if available.
    toolchain_id : str
        The toolchain (e.g., Xcode version) used for the build.
    sdk : str
        The SDK version used in the build.
    configuration : str
        The build configuration (e.g., Debug, Release).
    build_config_hash : str
        Hash of the build configuration to detect changes.
    index_store_path : str
        Filesystem path to the index store for this build.
    """

    build_id: str
    created_at: str
    commit_sha: str | None
    toolchain_id: str
    sdk: str
    configuration: str
    build_config_hash: str
    index_store_path: str


def freshness_for(
    conn, build_id: str, query_ctx: dict
) -> tuple[GraphFreshness, str]:
    """Determine the freshness of a build snapshot.

    Parameters
    ----------
    conn
        An open Ladybug connection.
    build_id : str
        The build snapshot identifier to check.
    query_ctx : dict
        Context dictionary containing expected values:
        - "toolchain_id": the required toolchain ID
        - "build_config_hash": the required build config hash

    Returns
    -------
    tuple[GraphFreshness, str]
        A tuple of (snapshot, status) where status is one of:
        - "fresh": snapshot matches all query context requirements
        - "stale": snapshot not found in database
        - "toolchain_mismatch": toolchain_id doesn't match query_ctx
        - "build_mismatch": build_config_hash doesn't match query_ctx
    """
    rows = conn.execute(
        "MATCH (b:BuildSnapshot {id: $id}) "
        "RETURN b.toolchain_id, b.build_config_hash, b.commit_sha, "
        "b.created_at, b.index_store_path, b.sdk, b.configuration",
        {"id": build_id},
    ).get_all()

    if not rows:
        empty = GraphFreshness(
            build_id=build_id,
            created_at="",
            commit_sha=None,
            toolchain_id="",
            sdk="",
            configuration="",
            build_config_hash="",
            index_store_path="",
        )
        return empty, "stale"

    row = rows[0]
    snapshot = GraphFreshness(
        build_id=build_id,
        created_at=row[3] or "",
        commit_sha=row[2],
        toolchain_id=row[0] or "",
        sdk=row[5] or "",
        configuration=row[6] or "",
        build_config_hash=row[1] or "",
        index_store_path=row[4] or "",
    )

    req_toolchain = query_ctx.get("toolchain_id", "")
    req_hash = query_ctx.get("build_config_hash", "")

    if req_toolchain and req_toolchain != snapshot.toolchain_id:
        return snapshot, "toolchain_mismatch"
    if req_hash and req_hash != snapshot.build_config_hash:
        return snapshot, "build_mismatch"

    return snapshot, "fresh"


def map_search_freshness(snapshot_status: str) -> str:
    """Map detailed snapshot freshness to the phase-1 guided-search values."""
    if snapshot_status == "fresh":
        return "fresh"
    if snapshot_status == "stale":
        return "stale"
    if snapshot_status in {"toolchain_mismatch", "build_mismatch"}:
        return "partially_stale"
    return "unknown"
