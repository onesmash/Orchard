"""Ingest state persistence for incremental updates.

Tracks the last successful ingest so that subsequent runs can skip
unchanged files.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _state_path(project_dir: str) -> Path:
    return Path(project_dir) / ".orchard" / "ingest-state.json"


def load_state(project_dir: str) -> dict[str, Any] | None:
    """Read the persisted ingest state, or *None* if not found."""
    path = _state_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(
    project_dir: str,
    last_ingest_ts: float,
    target: str,
    index_store_path: str,
    files: list[str] | None = None,
) -> None:
    """Persist ingest state after a successful ingest.

    *files* is optional — full ingests only save the timestamp; the file
    list is populated lazily on the first incremental run.
    """
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "last_ingest_ts": last_ingest_ts,
        "target": target,
        "index_store_path": index_store_path,
    }
    if files is not None:
        data["files"] = files
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def touch_timestamp() -> float:
    """Return the current Unix timestamp for use as *last_ingest_ts*."""
    return time.time()
