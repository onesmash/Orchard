"""
GraphFreshness tracking for build snapshots.

Provides freshness validation to determine if a build snapshot is current
relative to the requested toolchain and build configuration.
"""

from __future__ import annotations

from dataclasses import dataclass


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
        "b.created_at, b.index_store_path",
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
        sdk="",
        configuration="",
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
