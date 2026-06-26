"""Ingest state persistence for incremental updates.

Tracks the last successful ingest so that subsequent runs can skip
unchanged files.

State format (new multi-target)::

    {
        "targets": ["Zoom", "iOSLogin"],
        "last_ingest_ts": 1719400000.123,
        "index_store_paths": {"Zoom": "/path/to/IndexStore", "iOSLogin": "/path/..."}
    }

Legacy single-target format (still readable)::

    {
        "target": "Zoom",
        "last_ingest_ts": 1719400000.123,
        "index_store_path": "/path/to/IndexStore"
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _state_path(project_dir: str) -> Path:
    return Path(project_dir) / ".orchard" / "ingest-state.json"


def _normalize_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy single-target state to the multi-target format."""
    if "targets" in raw:
        # already in new format — ensure index_store_paths exists
        if "index_store_paths" not in raw:
            raw["index_store_paths"] = {}
        return raw
    # Legacy format: {"target": "X", "index_store_path": "..."}
    target = raw.get("target", "")
    index_store_path = raw.get("index_store_path", "")
    return {
        "targets": [target] if target else [],
        "last_ingest_ts": raw.get("last_ingest_ts", 0.0),
        "index_store_paths": {target: index_store_path} if target else {},
    }


def load_state(project_dir: str) -> dict[str, Any] | None:
    """Read the persisted ingest state, or *None* if not found.

    Returns the state normalised to the multi-target format regardless of
    whether it was saved in the legacy or current format.
    """
    path = _state_path(project_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return _normalize_state(raw)


def save_state(
    project_dir: str,
    last_ingest_ts: float,
    targets: list[str],
    index_store_paths: dict[str, str],
    files: list[str] | None = None,
) -> None:
    """Persist ingest state after a successful ingest.

    *targets* is a list of target identifiers that were ingested.
    *index_store_paths* maps each target to its IndexStore path.
    *files* is optional — full ingests only save the timestamp; the file
    list is populated lazily on the first incremental run.
    """
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "last_ingest_ts": last_ingest_ts,
        "targets": targets,
        "index_store_paths": index_store_paths,
    }
    if files is not None:
        data["files"] = files
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def touch_timestamp() -> float:
    """Return the current Unix timestamp for use as *last_ingest_ts*."""
    return time.time()
