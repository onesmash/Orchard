"""Xcode settings discovery — DerivedData path and workspace matching.

Inspired by SourceKit-LSP's BuildServerManager: instead of guessing,
read the build system's own configuration to find the IndexStore.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


def get_derived_data_path() -> str:
    """Return the Xcode DerivedData directory path.

    Reads the custom location from Xcode preferences, falling back to
    the default ``~/Library/Developer/Xcode/DerivedData/``.
    """
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.dt.Xcode", "IDECustomDerivedDataLocation"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return os.path.expanduser("~/Library/Developer/Xcode/DerivedData")


def find_xcode_project(cwd: str | None = None) -> str | None:
    """Walk up from *cwd* to find a ``.xcworkspace`` or ``.xcodeproj``.

    Searches within cwd and up to 3 ancestors, max 5 levels deep.
    Prefers matches closer to cwd.  Returns the absolute path, or None.
    """
    cwd = Path(cwd or os.getcwd()).resolve()
    candidates: list[tuple[float, str]] = []  # (depth_score, path)
    for depth, directory in enumerate([cwd, *cwd.parents][:4]):
        for suffix in (".xcworkspace", ".xcodeproj"):
            for entry in directory.glob(f"**/*{suffix}"):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                try:
                    rel_depth = len(entry.relative_to(directory).parts)
                except ValueError:
                    continue
                if rel_depth > 5:
                    continue
                # Score: closer ancestors × 100 + rel_depth (lower = closer)
                score = depth * 100 + rel_depth
                candidates.append((score, str(entry)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _project_paths_from(project: str) -> list[str]:
    """Return all possible Xcode paths that could match *project* in info.plist.

    *project* may be an ``.xcworkspace``; info.plist keys are the ``.xcodeproj``
    path.  Return both so either form matches.
    """
    p = Path(project)
    paths = [project]
    if p.suffix == ".xcworkspace":
        # Also try sibling xcodeproj of the same name.
        sibling = p.with_suffix(".xcodeproj")
        if sibling.is_dir():
            paths.insert(0, str(sibling))  # prefer xcodeproj match
    elif p.suffix == ".xcodeproj":
        sibling = p.with_suffix(".xcworkspace")
        if sibling.is_dir():
            paths.append(str(sibling))
    return paths


def match_derived_data(project_path: str) -> list[tuple[str, str, str]]:
    """Find DerivedData directories that belong to *project_path*.

    Scans ``get_derived_data_path()`` for directories whose ``info.plist``
    ``WorkspacePath`` matches *project_path*.  Xcode uses the project file
    path (``.xcodeproj``), not the workspace path, as the plist key.

    Returns a list of ``(derived_data_dir, datastore_path, last_accessed)``
    tuples sorted by *last_accessed* descending.
    """
    project_paths = _project_paths_from(project_path)
    project_name = Path(project_path).stem  # "Zoom" from "Zoom.xcodeproj"
    dd_root = Path(get_derived_data_path())
    if not dd_root.is_dir():
        return []

    candidates: list[tuple[str, str, str]] = []
    try:
        for entry in dd_root.iterdir():
            if not entry.is_dir() or not entry.name.startswith(f"{project_name}-"):
                continue
            plist_path = entry / "info.plist"
            if not plist_path.is_file():
                continue
            try:
                with open(plist_path, "rb") as fh:
                    plist = plistlib.load(fh)
            except (plistlib.InvalidFileException, OSError):
                continue
            ws_path = plist.get("WorkspacePath", "")
            if ws_path not in project_paths:
                continue
            datastore = entry / "Index.noindex" / "DataStore"
            if not datastore.is_dir():
                continue
            last_accessed = str(plist.get("LastAccessedDate", ""))
            candidates.append((str(entry), str(datastore), last_accessed))
    except OSError:
        return []

    # Sort by LastAccessedDate descending (newest first).
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates
