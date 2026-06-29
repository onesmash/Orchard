"""``orchard setup`` — one-shot configuration for Claude Code and Codex.

Configures the MCP server entry, installs the orchard skill, and injects
the orchard code-intelligence block into CLAUDE.md / AGENTS.md.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def cmd_setup(args: list[str]) -> None:
    """Entry point for ``orchard setup``."""
    import argparse

    ap = argparse.ArgumentParser(prog="orchard setup")
    ap.add_argument(
        "--mcp", action="store_true",
        help="Install only the MCP server entry",
    )
    ap.add_argument(
        "--skill", action="store_true",
        help="Install only the orchard skill",
    )
    ap.add_argument(
        "--model", action="store_true",
        help="Download the embedding model only",
    )
    ap.add_argument(
        "--claude-md", action="store_true",
        help="Inject the orchard code-intelligence block into CLAUDE.md / AGENTS.md",
    )
    ap.add_argument(
        "--project-dir", default=".",
        help="Project root for CLAUDE.md injection (default: cwd)",
    )
    ns = ap.parse_args(args)

    # When no flags are given, install everything.
    all_items = not (ns.mcp or ns.skill or ns.model or ns.claude_md)

    errors: list[str] = []
    installed: list[str] = []
    skipped: list[str] = []

    if all_items or ns.mcp:
        ok, msg = _setup_mcp()
        (installed if ok else errors).append(msg)
        ok2, msg2 = _setup_codex_mcp()
        (installed if ok2 else errors).append(msg2)

    if all_items or ns.skill:
        ok, msg = _setup_skill()
        (installed if ok else errors).append(msg)

    if all_items or ns.model:
        ok, msg = _setup_model()
        (installed if ok else errors).append(msg)

    if all_items or ns.claude_md:
        ok, msg = _setup_claude_md(Path(ns.project_dir).resolve())
        (installed if ok else errors).append(msg)

    # Summary
    print()
    for item in installed:
        print(f"  ✅ {item}")
    for item in errors:
        print(f"  ❌ {item}")
    print()

    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

_MCP_TARGET = Path.home() / ".claude.json"

_OLD_MCP_TARGET = Path.home() / ".claude" / "mcp.json"

_MCP_ENTRY = {
    "orchard": {
        "command": "orchard-mcp",
        "args": [],
    },
}


def _setup_mcp() -> tuple[bool, str]:
    """Ensure ``~/.claude.json`` includes the orchard MCP server.

    Returns ``(ok, message)``.
    """
    target = _MCP_TARGET

    if not target.exists():
        return False, f"MCP: {target} not found — is Claude Code installed?"

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"MCP: cannot read {target}: {e}"

    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return False, (
            f"MCP: {target} exists but contains invalid JSON. "
            "Please add the following entry manually to the 'mcpServers' key:\n"
            + json.dumps(_MCP_ENTRY, indent=2)
        )

    if config.get("mcpServers", {}).get("orchard"):
        msg = "MCP: already configured in ~/.claude.json (skipped)"
        # Clean up old ~/.claude/mcp.json if it exists
        if _OLD_MCP_TARGET.exists():
            _OLD_MCP_TARGET.unlink(missing_ok=True)
            msg += " — removed stale ~/.claude/mcp.json"
        return True, msg

    # Merge orchard into existing mcpServers
    config.setdefault("mcpServers", {}).update(_MCP_ENTRY)
    new_raw = json.dumps(config, indent=2) + "\n"

    try:
        target.write_text(new_raw, encoding="utf-8")
    except OSError as e:
        return False, f"MCP: cannot write {target}: {e}"

    # Clean up old ~/.claude/mcp.json
    if _OLD_MCP_TARGET.exists():
        _OLD_MCP_TARGET.unlink(missing_ok=True)

    return True, f"MCP: wrote {target}"


# ---------------------------------------------------------------------------
# Codex MCP
# ---------------------------------------------------------------------------

_CODEX_CONFIG = Path.home() / ".codex" / "config.toml"

_CODEX_MCP_ENTRY = """\
[mcp_servers.orchard]
type = "stdio"
command = "orchard-mcp"
args = []
"""


def _setup_codex_mcp() -> tuple[bool, str]:
    """Ensure ``~/.codex/config.toml`` includes the orchard MCP server.

    Returns ``(ok, message)``.
    """
    target = _CODEX_CONFIG

    if not target.exists():
        return True, "Codex MCP: ~/.codex/config.toml not found (skipped)"

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"Codex MCP: cannot read {target}: {e}"

    if "[mcp_servers.orchard]" in raw:
        return True, "Codex MCP: already configured (skipped)"

    # Append orchard entry
    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n" + _CODEX_MCP_ENTRY)
    except OSError as e:
        return False, f"Codex MCP: cannot write {target}: {e}"

    return True, f"Codex MCP: wrote {target}"


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

_SKILL_TARGETS = [
    Path.home() / ".claude" / "skills" / "orchard",
    Path.home() / ".agents" / "skills" / "orchard",
]


def _skill_source_dir() -> Path:
    """Return the path to the bundled skill directory.

    Works in two scenarios:

    1. **Wheel install**: ``orchard/setup.py`` and ``orchard/skills/orchard/``
       sit side-by-side in site-packages.
    2. **Dev install** (``pip install -e .``): ``setup.py`` is at
       ``src/orchard/setup.py`` while skills live at ``<repo>/skills/orchard/``
       (three levels up).
    """
    base = Path(__file__).resolve().parent

    # Wheel install: skills are adjacent to this module.
    pkg = base / "skills" / "orchard"
    if pkg.is_dir():
        return pkg

    # Dev install: walk up from src/orchard/ to repo root.
    dev = base.parent.parent / "skills" / "orchard"
    if dev.is_dir():
        return dev

    return pkg  # fallback — will produce a clear error in _setup_skill


def _setup_skill() -> tuple[bool, str]:
    """Copy the bundled orchard skill into ``~/.claude/skills/orchard/`` and
    ``~/.agents/skills/orchard/``.

    Returns ``(ok, message)``.
    """
    src = _skill_source_dir()

    if not src.is_dir():
        return False, f"Skill: source not found at {src}"

    installed = []
    for target in _SKILL_TARGETS:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, target, dirs_exist_ok=True)
            installed.append(str(target))
        except OSError as e:
            return False, f"Skill: copy to {target} failed: {e}"

    return True, f"Skill: installed to {', '.join(installed)}"


# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------

_MODEL_REPO = "Qwen/Qwen3-Embedding-0.6B-GGUF"
_MODEL_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"


def _setup_model() -> tuple[bool, str]:
    """Download the GGUF embedding model to ``~/.orchard/models/``.

    Returns ``(ok, message)``.
    """
    dest_dir = Path.home() / ".orchard" / "models"
    dest_path = dest_dir / _MODEL_FILE

    if dest_path.exists():
        size_mb = dest_path.stat().st_size / 1e6
        return True, f"Model: already downloaded ({size_mb:.0f} MB, skipped)"

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return False, (
            "Model: huggingface_hub is required for model download. "
            "Install it with: pip install huggingface_hub"
        )

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        hf_hub_download(
            repo_id=_MODEL_REPO,
            filename=_MODEL_FILE,
            local_dir=str(dest_dir),
        )
    except Exception as e:
        return False, f"Model: download failed: {e}"

    return True, f"Model: downloaded to {dest_path}"


# ---------------------------------------------------------------------------
# CLAUDE.md / AGENTS.md injection
# ---------------------------------------------------------------------------

_ORCHARD_BLOCK_START = "<!-- orchard:start -->"
_ORCHARD_BLOCK_END = "<!-- orchard:end -->"

_ORCHARD_BLOCK = """<!-- orchard:start -->
# Orchard — Apple Semantic Graph

This project is indexed by orchard as **{project_name}** ({symbol_count:,} symbols, {calls_count:,} calls, {contains_count:,} contains). Use the orchard MCP tools to understand code, assess impact, and navigate safely.

> If the index is stale, run `orchard ingest --project-dir .` to rebuild.
> Data source: Xcode IndexStore — every edge is compiler-verified with confidence labels.

## Ingest Basics

- `orchard ingest --project-dir .` rebuilds the graph into `.orchard/graph.db`
- `--db` points to Orchard's graph database file, usually `.orchard/graph.db`
- `--index-store` points to Xcode's IndexStore `.../Index.noindex/DataStore`
- Do not pass a DerivedData directory to `--db`; that is usually an `--index-store` hint instead

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `orchard_impact` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST use orchard to find callers/callees** when exploring unfamiliar code — `orchard_find_callers` and `orchard_find_callees` with compiler-verified edges are more precise than grep. Check the `confidence` field to distinguish source-level evidence from compiler-inferred edges.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.

## When Debugging

1. `orchard_search({{name: "<symbol>"}})` — find the symbol's USR; results include `by_kind` grouping
2. `orchard_find_callers({{usr: "<USR>"}})` — see who calls it; each entry has `confidence` (compiler-verified / inferred)
3. `orchard_find_callees({{usr: "<USR>"}})` — see what it calls; ObjC callees carry `semantic_role` (notification_observer, delegate_setter, framework_callback...) and notification_bridges (who registered → selector → event key → callback) by default
4. `orchard_impact({{usr: "<USR>"}})` — assess blast radius with depth groups

## When Debugging Notifications

1. `orchard notification-graph -n "<name>"` — CLI: find who posts and observes a notification
2. `orchard_notification_graph({{notification_name: "<name>"}})` — MCP: same data, grouped by notification name
3. Or query directly: `MATCH (p:Symbol)-[:Posts]->(n:Notification {{name: "kNoti_X"}})-[:Observes]->(cb:Symbol) RETURN p.name, cb.name`
4. Observer-only notifications: `MATCH (n:Notification) WHERE NOT EXISTS {{ MATCH (:Symbol)-[:Posts]->(n) }} RETURN n.name`

## When Refactoring

- **Before editing**: run `orchard_impact` on the target symbol to find all dependents.
- **After changes**: run `orchard_find_callers` to verify no unexpected new dependents broke.
- **Cross-language bridges**: use `orchard_find_references` to see ObjC ↔ Swift bridge edges.
- **Renaming**: use `orchard_rename` — USR-precise, dry-run first, uses Symbol+Calls tables (no Occurrence data needed).
- **Notification callbacks**: use `orchard notification-graph` (CLI) or `orchard_notification_graph` (MCP) to find @selector registrations and verify callback wiring.

## Never Do

- NEVER edit a function, class, or method without first running `orchard_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER commit changes without verifying the impact scope.
- NEVER trust grep over orchard for caller/callee queries — orchard edges are compiler-verified.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `search` | Find symbols by name | `orchard_search({{name: "viewDidLoad"}})` |
| `find_callers` | Who calls this symbol | `orchard_find_callers({{usr: "<USR>"}})` |
| `find_callees` | What this symbol calls (returns notification_bridges by default) | `orchard_find_callees({{usr: "<USR>"}})` |
| `find_references` | Incoming + outgoing references (with semantic_role for ObjC) | `orchard_find_references({{usr: "<USR>"}})` |
| `impact` | Blast radius before editing | `orchard_impact({{usr: "<USR>"}})` |
| `symbol` | Symbol metadata | `orchard_symbol({{usr: "<USR>"}})` |
| `hierarchy` | Type hierarchy | `orchard_hierarchy({{usr: "<USR>"}})` |
| `rename` | USR-precise rename (dry-run safe) | `orchard_rename({{usr: "<USR>", new_name: "X"}})` |
| `stats` | Database overview | `orchard_stats()` |
| `audit` | Module coverage gaps | `orchard_audit({{project_dir: "."}})` |
| `notification-graph` | Find @selector / notification wiring | `orchard notification-graph [-n <name>]` |
| `notification_graph` | Notification wiring: who registers → selector → event → callback | `orchard_notification_graph({{group_by: "observer"}})` |

## Graph Schema

| Node | Purpose |
|------|---------|
| `Symbol` | Compiler-verified symbol (class, method, function...) |
| `Notification` | Notification name extracted from source |
| `File` | Source file path |

| Edge | Meaning | Source |
|------|---------|--------|
| `Calls` | A calls B | IndexStore (compiler-verified) |
| `Posts` | A posts notification N | grep @selector (derive/notification) |
| `Observes` | N notifies callback C | grep @selector (derive/notification) |
| `Contains` | Class contains method | IndexStore |
| `Inherits` / `Implements` / `Extends` | Type relations | IndexStore |
| `BridgesTo` | ObjC ↔ Swift bridge | derive/bridge |

## Confidence Labels

Every caller/callee carries a `confidence` field:

| confidence | Meaning |
|-----------|---------|
| `compiler-verified` | Observed at a source-level call-site (source_direct or symbolgraph) |
| `inferred` | Compiler type-inference edge (protocol dispatch, overrides) |

Set `include_inferred: true` to see both; default shows only compiler-verified.

## Semantic Roles (ObjC callees)

ObjC callees in `find_callees` and `find_references` (outgoing) carry a `semantic_role` field inline:

| role | Example |
|------|---------|
| `notification_observer` | `addObserver:selector:name:object:` |
| `notification_poster` | `postNotificationName:object:` |
| `target_action` | `addTarget:action:forControlEvents:` |
| `delegate_setter` | `setDelegate:` |
| `framework_callback` | `viewDidLoad`, `application:didFinish...` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d1 | WILL BREAK — direct callers, subtypes, protocol conformers | MUST update these |
| d2 | LIKELY AFFECTED — callers of callers | Should test |
| d3+ | MAY NEED TESTING — transitive dependents | Test if critical path |

## Ingest Progress

During a real ingest, progress appears in phases instead of staying silent:

- `ingest: reading index store...`
- streamed `orchard-indexstore-reader` progress lines from stderr
- `communities: deriving graph partitions...`
- `notification-graph: scanning source files...`
- `processes: detecting execution flows...`

If ingest looks "stuck", first check which phase is currently running rather than
assuming the whole command is hung.

## Keeping the Index Fresh

`orchard ingest` writes a `BuildSnapshot` for the current graph build. After a
successful ingest, normal queries such as `symbol`, `impact`, `find_callers`,
and `find_callees` should usually return `freshness: "fresh"` unless the graph
is genuinely outdated or the query uses a mismatched build context.

After committing code changes, re-run ingest to update:

```bash
orchard ingest --project-dir .
```

<!-- orchard:end -->"""


def _resolve_db(project_dir: Path) -> str | None:
    """Return the path to the orchard graph DB for *project_dir*, or None.

    Priority: ``.orchard/graph.db`` in *project_dir*, then walk up.
    """
    for directory in [project_dir, *project_dir.parents]:
        db = directory / ".orchard" / "graph.db"
        if db.exists():
            return str(db)
    return None


def _collect_stats(project_dir: Path) -> dict[str, int]:
    """Return symbol/calls/contains counts by reading the project's graph DB."""
    from orchard.graph.db import get_connection

    db_path = _resolve_db(project_dir)
    if db_path is None:
        return {}
    conn = get_connection(db_path)
    try:
        sym = conn.execute("MATCH (s:Symbol) RETURN count(s)").get_all()[0][0]
        calls = conn.execute("MATCH ()-[r:Calls]->() RETURN count(r)").get_all()[0][0]
        contains = conn.execute("MATCH ()-[r:Contains]->() RETURN count(r)").get_all()[0][0]
        return {"symbol_count": sym, "calls_count": calls, "contains_count": contains}
    finally:
        conn.close()


def _upsert_block(path: Path, block: str) -> bool:
    """Insert or update the orchard block in *path*.  Returns True if written."""
    start = _ORCHARD_BLOCK_START
    end = _ORCHARD_BLOCK_END

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""

    if start in text and end in text:
        # Replace existing block in-place.
        before = text[: text.index(start)]
        after = text[text.index(end) + len(end):]
        new_text = before + block + after
    else:
        # Append at the end.
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + "\n" + block + "\n"

    if new_text == text:
        return False  # idempotent

    path.write_text(new_text, encoding="utf-8")
    return True


def _setup_claude_md(project_dir: Path) -> tuple[bool, str]:
    """Inject the orchard code-intelligence block into CLAUDE.md and AGENTS.md.

    Stats are read from the project's ``.orchard/graph.db`` so the block
    carries live symbol / call / containment counts.

    Returns ``(ok, message)``.
    """
    stats = _collect_stats(project_dir)
    if not stats:
        return False, (
            "CLAUDE.md: no orchard database found. "
            "Run `orchard ingest --project-dir .` first."
        )

    project_name = project_dir.name
    block = _ORCHARD_BLOCK.format(project_name=project_name, **stats)

    updated: list[str] = []
    for md_name in ("CLAUDE.md", "AGENTS.md"):
        target = project_dir / md_name
        try:
            if _upsert_block(target, block):
                updated.append(md_name)
        except OSError as e:
            return False, f"CLAUDE.md: cannot write {target}: {e}"

    if not updated:
        return True, "CLAUDE.md / AGENTS.md: already up-to-date (skipped)"

    return True, f"CLAUDE.md / AGENTS.md: injected orchard block into {', '.join(updated)}"
