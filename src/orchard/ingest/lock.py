from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import fcntl


LOCK_BUSY_EXIT_CODE = 23


def graph_db_lock_path(graph_db_path: str) -> str:
    normalized = Path(graph_db_path).expanduser().resolve()
    digest = hashlib.sha256(str(normalized).encode("utf-8")).hexdigest()
    lock_dir = Path.home() / ".orchard" / "locks"
    return str(lock_dir / f"orchard-ingest-{digest}.lock")


@dataclass
class GraphDBLock:
    handle: TextIO

    def __enter__(self) -> GraphDBLock:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def try_acquire_graph_db_lock(graph_db_path: str) -> GraphDBLock | None:
    lock_path = Path(graph_db_lock_path(graph_db_path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return GraphDBLock(handle)
