"""Xcode settings discovery — DerivedData path and workspace matching.

Inspired by SourceKit-LSP's BuildServerManager: instead of guessing,
read the build system's own configuration to find the IndexStore.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path


def infer_derived_data_root(index_store_path: str) -> str | None:
    """Infer the DerivedData entry root from an IndexStore DataStore path."""
    path = Path(index_store_path)
    if path.name != "DataStore":
        return None
    if path.parent.name != "Index.noindex":
        return None
    return str(path.parent.parent)


def _top_level_build_dirs(root: Path) -> list[Path]:
    return [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and entry.name.endswith(".build")
    ]


def _split_dep_tokens(raw: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    escape = False
    for ch in raw.replace("\\\n", " "):
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch.isspace():
            if current:
                tokens.append("".join(current))
                current.clear()
            continue
        current.append(ch)
    if escape:
        current.append("\\")
    if current:
        tokens.append("".join(current))
    return tokens


def discover_compiled_targets(derived_data_root: str) -> list[str]:
    """Return compiled target names from ``*.build`` directories."""
    root = Path(derived_data_root) / "Index.noindex" / "Build" / "Intermediates.noindex"
    if not root.is_dir():
        return []
    targets = {
        entry.name[:-6]
        for entry in _top_level_build_dirs(root)
    }
    return sorted(targets)


def discover_compiled_files(derived_data_root: str, targets: list[str]) -> list[str]:
    """Collect source paths from dependency files for selected targets."""
    root = Path(derived_data_root) / "Index.noindex" / "Build" / "Intermediates.noindex"
    if not root.is_dir():
        return []
    selected = set(targets)
    sources: set[str] = set()
    for target_name in selected:
        build_dir = root / f"{target_name}.build"
        if not build_dir.is_dir():
            continue
        for dep_file in build_dir.rglob("*.d"):
            try:
                content = dep_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            _, _, dependencies = content.partition(":")
            for token in _split_dep_tokens(dependencies):
                if token.endswith((".c", ".cc", ".cpp", ".m", ".mm", ".swift")):
                    sources.add(token)
    return sorted(sources)


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


def _search_xcode_projects(root: Path, max_depth: int = 5) -> list[str]:
    """Return matching ``.xcworkspace``/``.xcodeproj`` paths under *root*.

    Uses iterative BFS so we return early when a match is found, avoiding
    a full-tree walk on large directories.
    """
    from collections import deque
    results: list[str] = []
    visited: set[str] = {str(root)}
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue:
        directory, depth = queue.popleft()
        if depth >= max_depth:
            continue
        try:
            entries = list(directory.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            key = str(entry)
            if key in visited:
                continue
            visited.add(key)
            if entry.is_dir():
                if entry.name.startswith("."):
                    continue
                if entry.suffix in (".xcworkspace", ".xcodeproj"):
                    results.append(str(entry))
                else:
                    queue.append((entry, depth + 1))
        if results and depth >= 1:
            break  # found something, stop searching deeper
    return results


def find_xcode_project(cwd: str | None = None) -> str | None:
    """Walk up from *cwd* to find a ``.xcworkspace`` or ``.xcodeproj``.

    Searches within cwd and up to 3 ancestors using iterative BFS (max 5
    levels deep).  Prefers matches closer to cwd.  Returns the absolute
    path, or None.
    """
    cwd = Path(cwd or os.getcwd()).resolve()
    home = Path.home()
    ancestors = [cwd]
    for p in cwd.parents:
        if not str(p).startswith(str(home) + os.sep) and p != home:
            break
        ancestors.append(p)
        if len(ancestors) >= 4:
            break
    candidates: list[tuple[int, str]] = []  # (score, path)
    for depth, directory in enumerate(ancestors):
        found = _search_xcode_projects(directory, max_depth=5)
        if not found and depth > 0:
            continue  # no xcode project under this ancestor
        for entry in found:
            try:
                rel_depth = len(Path(entry).relative_to(directory).parts)
            except ValueError:
                rel_depth = 99
            score = depth * 100 + rel_depth
            candidates.append((score, entry))
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
        nested = p.parent / p.stem / f"{p.stem}.xcodeproj"
        if nested.is_dir():
            paths.insert(0, str(nested))
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

    candidates: list[tuple[str, str, str, int]] = []
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
            size_bytes = sum(p.stat().st_size for p in datastore.rglob("*") if p.is_file())
            candidates.append((str(entry), str(datastore), last_accessed, size_bytes))
    except OSError:
        return []

    # Sort by LastAccessedDate descending, then by datastore size descending.
    candidates.sort(key=lambda x: (x[2], x[3]), reverse=True)
    return [(dd, ds, acc) for dd, ds, acc, _ in candidates]


def _project_search_roots(project_path: str) -> list[Path]:
    project = Path(project_path).resolve()
    if project.suffix == ".xcodeproj":
        return [project]
    if project.suffix == ".xcworkspace":
        roots = [project.parent]
        parent = project.parent.parent
        if parent not in roots:
            roots.append(parent)
        return roots
    roots: list[Path] = [project] if project.is_dir() else []
    parent = project.parent
    if parent not in roots:
        roots.append(parent)
    grandparent = parent.parent
    if grandparent != parent and grandparent not in roots:
        roots.append(grandparent)
    return roots


def _workspace_project_root(project_path: str) -> Path | None:
    project = Path(project_path).resolve()
    if project.suffix == ".xcworkspace":
        return project.parent
    return None


def _candidate_pbxproj_paths(project_path: str) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in _project_search_roots(project_path):
        if root.suffix == ".xcodeproj":
            pbxproj = root / "project.pbxproj"
            if pbxproj.is_file() and pbxproj not in seen:
                seen.add(pbxproj)
                candidates.append(pbxproj)
            continue
        for xcodeproj in root.rglob("*.xcodeproj"):
            pbxproj = xcodeproj / "project.pbxproj"
            if pbxproj.is_file() and pbxproj not in seen:
                seen.add(pbxproj)
                candidates.append(pbxproj)
    return candidates


def _parse_pbxproj_objects(text: str) -> dict[str, str]:
    pattern = re.compile(r"^\s*(?P<object_id>[A-Za-z0-9_]+)\s*/\*.*?\*/\s*=\s*\{", re.MULTILINE)
    objects: dict[str, str] = {}
    for match in pattern.finditer(text):
        depth = 1
        pos = match.end()
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            continue
        objects[match.group("object_id")] = text[match.end():pos - 1]
    return objects


def _object_isa(body: str) -> str:
    match = re.search(r"^\s*isa\s*=\s*([A-Za-z0-9_]+);", body, re.MULTILINE)
    return match.group(1) if match else ""


def _object_scalar(body: str, key: str) -> str:
    match = re.search(rf"(?:(?<=^)|(?<=\n)|(?<=;))\s*{re.escape(key)}\s*=\s*(.+?);", body, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"')


def _object_array_ids(body: str, key: str) -> list[str]:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*\((?P<items>.*?)^\s*\);", body, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    items = match.group("items")
    return [
        item.group(1)
        for item in re.finditer(r"^\s*([A-Za-z0-9_]+)\b", items, re.MULTILINE)
    ]


def _source_root_base(pbxproj_path: Path, raw_path: str) -> Path:
    base = pbxproj_path.parent.parent
    # Generated iOS projects often live under auto_ios/*.xcodeproj while
    # SOURCE_ROOT-backed entries still point at the repository's src tree.
    if base.name == "auto_ios" and raw_path.startswith("src/"):
        return base.parent
    return base


def _collapse_source_paths(file_paths: list[Path]) -> list[str]:
    if not file_paths:
        return []
    directories = [path.parent if path.suffix else path for path in file_paths]
    common = Path(os.path.commonpath([str(path) for path in directories]))
    anchor_names = {"src", "Sources", "source", "Source", "include", "Include", "Headers", "headers", "sdk", "SDK"}
    for candidate in (common, *common.parents):
        if candidate.name in anchor_names:
            return [str(candidate)]
    return [str(common)]


def _normalize_target_root(root: str, project_root: Path | None) -> str:
    if project_root is None:
        return root
    root_path = Path(root)
    try:
        root_path.relative_to(project_root)
    except ValueError:
        return root
    return str(project_root)


def _target_source_root_directories(
    objects: dict[str, str],
    pbxproj_path: Path,
    targets: set[str],
    project_root: Path | None,
) -> list[str]:
    file_dirs: set[str] = set()
    pbxproj_in_project_root = False
    if project_root is not None:
        try:
            pbxproj_path.resolve().relative_to(project_root)
            pbxproj_in_project_root = True
        except ValueError:
            pbxproj_in_project_root = False
    for object_id, body in objects.items():
        if _object_isa(body) != "PBXNativeTarget":
            continue
        target_name = _object_scalar(body, "name")
        product_name = _object_scalar(body, "productName")
        if target_name not in targets and product_name not in targets:
            continue
        target_files: list[Path] = []
        for phase_id in _object_array_ids(body, "buildPhases"):
            phase_body = objects.get(phase_id, "")
            if _object_isa(phase_body) != "PBXSourcesBuildPhase":
                continue
            for build_file_id in _object_array_ids(phase_body, "files"):
                build_file_body = objects.get(build_file_id, "")
                if _object_isa(build_file_body) != "PBXBuildFile":
                    continue
                file_ref_id = _object_scalar(build_file_body, "fileRef").split()[0]
                file_ref_body = objects.get(file_ref_id, "")
                if _object_isa(file_ref_body) != "PBXFileReference":
                    continue
                if _object_scalar(file_ref_body, "sourceTree") != "SOURCE_ROOT":
                    continue
                raw = _object_scalar(file_ref_body, "path")
                if not raw or raw.startswith("$("):
                    continue
                source_root = _source_root_base(pbxproj_path, raw)
                resolved = (source_root / raw).resolve()
                target_files.append(resolved)
        collapsed = _collapse_source_paths(target_files)
        if pbxproj_in_project_root and project_root is not None:
            collapsed = [str(project_root)]
        file_dirs.update(_normalize_target_root(root, project_root) for root in collapsed)
    return sorted(file_dirs)


def resolve_source_roots_for_targets(project_path: str, targets: list[str]) -> list[str]:
    """Resolve SOURCE_ROOT-backed directories from xcodeproj config for targets."""
    if not targets:
        return []
    roots: set[str] = set()
    requested = set(targets)
    project_root = _workspace_project_root(project_path)
    for pbxproj_path in _candidate_pbxproj_paths(project_path):
        try:
            text = pbxproj_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        objects = _parse_pbxproj_objects(text)
        roots.update(_target_source_root_directories(objects, pbxproj_path, requested, project_root))
    return sorted(roots)
