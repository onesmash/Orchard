"""Ingest state persistence for incremental updates.

Tracks the last successful ingest so that subsequent runs can skip
unchanged files.

State format::

    {
        "compiled_targets": ["MyApp", "MyLogin"],
        "last_ingest_ts": 1719400000.123,
        "index_store_path": "/path/to/IndexStore"
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
def _state_path(project_dir: str) -> Path:
    return Path(project_dir) / ".orchard" / "ingest-state.json"


def _candidate_output_paths_manifest_path(project_dir: str) -> Path:
    return Path(project_dir) / ".orchard" / "candidate-output-paths.json"


def load_state(project_dir: str) -> dict[str, object] | None:
    """Read the persisted ingest state, or *None* if not found.
    """
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
    compiled_targets: list[str],
    index_store_path: str,
    files: list[str] | None = None,
) -> None:
    """Persist ingest state after a successful ingest.

    *compiled_targets* is the compiled scope ingested in this run.
    *index_store_path* is the IndexStore used for that scope.
    *files* is optional — full ingests only save the timestamp; the file
    list is populated lazily on the first incremental run.
    """
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "last_ingest_ts": last_ingest_ts,
        "compiled_targets": compiled_targets,
        "index_store_path": index_store_path,
    }
    if files is not None:
        data["files"] = files
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_candidate_output_paths_manifest(
    project_dir: str,
    index_store_path: str,
    compiled_targets: list[str],
    mappings: list[dict[str, str]],
) -> None:
    """Persist candidate output-path mappings produced by a full ingest."""
    path = _candidate_output_paths_manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    output_paths = sorted({m["output_file"] for m in mappings if m.get("output_file")})
    data: dict[str, object] = {
        "index_store_path": index_store_path,
        "compiled_targets": compiled_targets,
        "output_paths": output_paths,
        "mappings": mappings,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def touch_timestamp() -> float:
    """Return the current Unix timestamp for use as *last_ingest_ts*."""
    return time.time()
