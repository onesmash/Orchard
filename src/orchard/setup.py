"""``orchard setup`` — one-shot configuration for Claude Code.

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

_MCP_TARGET = Path.home() / ".claude" / "mcp.json"

_MCP_ENTRY = {
    "mcpServers": {
        "orchard": {
            "command": "orchard-mcp",
            "args": [],
        },
    },
}


def _setup_mcp() -> tuple[bool, str]:
    """Ensure ``~/.claude/mcp.json`` includes the orchard MCP server.

    Returns ``(ok, message)``.
    """
    target = _MCP_TARGET

    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"MCP: cannot read {target}: {e}"

        try:
            config = json.loads(raw)
        except json.JSONDecodeError:
            return False, (
                f"MCP: {target} exists but contains comments or invalid JSON. "
                "Please add the following entry manually:\n"
                + json.dumps(_MCP_ENTRY, indent=2)
            )

        if "mcpServers" in config and "orchard" in config["mcpServers"]:
            return True, "MCP: already configured (skipped)"

        # Merge
        config.setdefault("mcpServers", {}).update(_MCP_ENTRY["mcpServers"])
        new_raw = json.dumps(config, indent=2) + "\n"
    else:
        new_raw = json.dumps(_MCP_ENTRY, indent=2) + "\n"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_raw, encoding="utf-8")
    except OSError as e:
        return False, f"MCP: cannot write {target}: {e}"

    return True, f"MCP: wrote {target}"


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

_SKILL_TARGET = Path.home() / ".claude" / "skills" / "orchard"


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
    """Copy the bundled orchard skill into ``~/.claude/skills/orchard/``.

    Returns ``(ok, message)``.
    """
    target = _SKILL_TARGET
    src = _skill_source_dir()

    if not src.is_dir():
        return False, f"Skill: source not found at {src}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=True)
    except OSError as e:
        return False, f"Skill: copy failed: {e}"

    return True, f"Skill: installed to {target}"


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
> Data source: Xcode IndexStore — every edge is compiler-verified, not heuristic.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `orchard_impact` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST use orchard to find callers/callees** when exploring unfamiliar code — `orchard_find_callers` and `orchard_find_callees` with compiler-verified edges are more precise than grep.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.

## When Debugging

1. `orchard_search({{name: "<symbol>"}})` — find the symbol's USR
2. `orchard_find_callers({{usr: "<USR>"}})` — see who calls it (default: source-level call evidence only)
3. `orchard_find_callees({{usr: "<USR>"}})` — see what it calls
4. `orchard_impact({{usr: "<USR>"}})` — assess blast radius with depth groups

## When Refactoring

- **Before editing**: run `orchard_impact` on the target symbol to find all dependents.
- **After changes**: run `orchard_find_callers` to verify no unexpected new dependents broke.
- **Cross-language bridges**: use `orchard_find_references` to see ObjC ↔ Swift bridge edges.

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
| `find_callees` | What this symbol calls | `orchard_find_callees({{usr: "<USR>"}})` |
| `impact` | Blast radius before editing | `orchard_impact({{usr: "<USR>"}})` |
| `symbol` | Symbol metadata | `orchard_symbol({{usr: "<USR>"}})` |
| `hierarchy` | Type hierarchy | `orchard_hierarchy({{usr: "<USR>"}})` |
| `stats` | Database overview | `orchard_stats()` |

## Signal Filtering

| Parameter | Default | Effect |
|-----------|---------|--------|
| `include_noise` | false | Show C++ operators & logging helpers |
| `include_inferred` | false | Show compiler-inferred edges |

By default only source-level call evidence is shown — compiler-verified call sites, not heuristics.

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d1 | WILL BREAK — direct callers, subtypes, protocol conformers | MUST update these |
| d2 | LIKELY AFFECTED — callers of callers | Should test |
| d3+ | MAY NEED TESTING — transitive dependents | Test if critical path |

## Keeping the Index Fresh

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
