"""``orchard setup`` — one-shot configuration for Claude Code and Codex.

Configures the MCP server entry, installs the bundled Orchard skills, and
injects the Orchard code-intelligence block into CLAUDE.md / AGENTS.md.
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
        help="Install only the bundled Orchard skills",
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

_BUNDLED_SKILL_NAMES = [
    "orchard",
    "orchard-cli",
    "orchard-debugging",
    "orchard-exploring",
    "orchard-impact-analysis",
]

_SKILL_TARGET_ROOTS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
]


def _skill_source_dir(skill_name: str) -> Path:
    """Return the path to one bundled skill directory.

    Works in two scenarios:

    1. **Wheel install**: ``orchard/setup.py`` and ``orchard/skills/<name>/``
       sit side-by-side in site-packages.
    2. **Dev install** (``pip install -e .``): ``setup.py`` is at
       ``src/orchard/setup.py`` while skills live at ``<repo>/skills/<name>/``
       (three levels up).
    """
    base = Path(__file__).resolve().parent

    # Wheel install: skills are adjacent to this module.
    pkg = base / "skills" / skill_name
    if pkg.is_dir():
        return pkg

    # Dev install: walk up from src/orchard/ to repo root.
    dev = base.parent.parent / "skills" / skill_name
    if dev.is_dir():
        return dev

    return pkg  # fallback — will produce a clear error in _setup_skill


def _setup_skill() -> tuple[bool, str]:
    """Copy bundled Orchard skills into Claude/Codex skill directories.

    Returns ``(ok, message)``.
    """
    installed = []
    for skill_name in _BUNDLED_SKILL_NAMES:
        src = _skill_source_dir(skill_name)
        if not src.is_dir():
            return False, f"Skill: source not found at {src}"

        for root in _SKILL_TARGET_ROOTS:
            target = root / skill_name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, target, dirs_exist_ok=True)
                installed.append(str(target))
            except OSError as e:
                return False, f"Skill: copy to {target} failed: {e}"

    return True, (
        "Skill: installed bundled Orchard skills "
        f"({', '.join(_BUNDLED_SKILL_NAMES)}) to {', '.join(installed)}"
    )


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

This project is indexed by orchard as **{project_name}**. Use Orchard MCP tools for compiler-indexed code navigation, deterministic symbol graph enrichment, and impact analysis.

> Data source: Xcode IndexStore. If freshness is stale/unknown, run `orchard ingest --project-dir .`.

## Always Do

- Before editing a function, class, or method, run `orchard_impact` and report direct callers, affected surfaces, and risk.
- Use `orchard_find_callers` / `orchard_find_callees` before grep when exploring unfamiliar code; Orchard edges come from IndexStore.
- When `orchard_search` misses, read `status`, `diag`, `candidates`, and `next`; a miss may be stale, uncovered, or only partially resolved.
- Warn the user before proceeding if impact returns HIGH or CRITICAL risk.

## Debugging Flow

1. `orchard_search({{name: "<symbol>"}})` — guided symbol lookup with `status`, `diag`, `candidates`, and `next`
2. If the user has a single stack frame, use `orchard_lookup_frame({{frame: "<stack line>"}})` to resolve owner/method candidates and graph context
3. If the user pasted a full crashlog or crash thread block, extract a concrete frame, symbol name, qualified name, or USR outside Orchard first. full crashlogs are handled outside Orchard.
4. `orchard_find_callers({{usr: "<USR>"}})` — see who calls it; each entry has `confidence` (compiler-verified / inferred), `call_style`, optional `execution_boundary`, and `source_scope`; if `data` is empty, inspect `dynamic_binding_hints` before concluding the callback is unreachable
5. `orchard_find_callees({{usr: "<USR>"}})` — see what it calls; ObjC callees carry `semantic_role` (notification_observer, delegate_setter, framework_callback...) and notification_bridges / target_action_bridges when Orchard can recover the wiring
6. `orchard_impact({{usr: "<USR>"}})` — assess blast radius with depth groups plus compact `summary`

## Frame Lookup Boundary

- `orchard_lookup_frame` accepts one single stack frame or frame-like symbol text.
- full crashlogs are handled outside Orchard; pass a single frame or explicit symbol identity to Orchard.
- Caller/callee results may include `call_style: synchronous_call` or `async_or_callback_boundary`.
- `execution_boundary.role` is heuristic and helps identify SDK callbacks, worker-thread dispatch, main-thread tasks, notification/callback sinks, and lifecycle/uninit paths.
- `source_scope.status: outside_workspace_root` means the indexed symbol's source is outside the current workspace root; grep under cwd may not find it.
- Do not claim Orchard has exact C++ object field offsets from IndexStore. Treat addresses such as `0x20` as hypotheses only; exact class/member offsets require DWARF, Clang record layout output, or another ABI-aware source.

## Guided Miss-Path

- `freshness` says whether the snapshot is trustworthy; `coverage` says whether the graph likely covers the searched scope.
- Prefer Orchard `next` actions over ad-hoc grep. If `next` recommends refresh, run `orchard ingest --project-dir .`.
- If `source_scope` is `outside_workspace_root`, the symbol may live in a sibling checkout even when grep under cwd fails. For callback-style methods with no static callers, use `orchard_notification_graph` for notification wiring and `orchard_target_action_graph` for UIKit action wiring.

## When Refactoring

- **Before editing**: run `orchard_impact` on the target symbol to find all dependents.
- **After changes**: run `orchard_find_callers` to verify no unexpected new dependents broke.
- **Cross-language bridges**: use `orchard_find_references` to see ObjC ↔ Swift bridge edges.
- **Renaming**: use `orchard rename` CLI — USR-precise, dry-run first, uses Symbol+Calls tables (no Occurrence data needed).

## Never Do

- Do not edit a function, class, or method without first running `orchard_impact`.
- Do not ignore HIGH or CRITICAL risk.
- Do not commit code changes without verifying impact scope.
- Do not trust grep over Orchard for caller/callee relationships.
- Do not assert exact C++ member byte offsets from Orchard/IndexStore data.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `search` | Guided symbol lookup by name or qualified name | `orchard_search({{name: "viewDidLoad"}})` |
| `lookup_frame` | Resolve a single stack frame to owner/method candidates and graph context | `orchard_lookup_frame({{frame: "ssb::thread_wrapper_t::process_msg(unsigned int)"}})` |
| `find_callers` | Who calls this symbol | `orchard_find_callers({{usr: "<USR>"}})` |
| `find_callees` | What this symbol calls; ObjC callees include semantic roles / notification bridges | `orchard_find_callees({{usr: "<USR>"}})` |
| `find_references` | Incoming + outgoing references (with semantic_role for ObjC) | `orchard_find_references({{usr: "<USR>"}})` |
| `impact` | Blast radius before editing; includes `data.summary` and `by_depth` | `orchard_impact({{usr: "<USR>"}})` |
| `symbol` | Symbol metadata: name, kind, language, module, file_path | `orchard_symbol({{usr: "<USR>"}})` |
| `hierarchy` | Type hierarchy: superclasses, protocols, subclasses | `orchard_hierarchy({{usr: "<USR>"}})` |
| `notification_graph` | Notification wiring: who registers → selector → event → callback | `orchard_notification_graph({{group_by: "observer"}})` |
| `target_action_graph` | UIKit target-action wiring: who registers → selector → control event → callback | `orchard_target_action_graph({{group_by: "callback"}})` |

## Key Labels

- `confidence`: `compiler-verified` or `inferred`; set `include_inferred: true` to see both.
- `semantic_role`: ObjC selector role such as `notification_observer`, `delegate_setter`, `target_action`, or `framework_callback`.
- `call_style`: `synchronous_call` vs `async_or_callback_boundary`.
- `execution_boundary.role`: `sdk_callback`, `worker_thread_dispatch`, `main_thread_task`, `notification_callback_sink`, or `lifecycle_uninit_path`.
- `source_scope.status`: `inside_workspace_root`, `outside_workspace_root`, or `unknown`.

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d1 | WILL BREAK — direct callers, subtypes, protocol conformers | MUST update these |
| d2 | LIKELY AFFECTED — callers of callers | Should test |
| d3+ | MAY NEED TESTING — transitive dependents | Test if critical path |

Impact output includes `data.summary` with `risk`, `direct_callers`, `primary_surface`, `d2_clusters`, and `likely_tests`. Use it for the first human-facing summary, then cite `by_depth` for the detailed blast radius.

After committing code changes, re-run `orchard ingest --project-dir .` to update the graph.

<!-- orchard:end -->"""

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

    Returns ``(ok, message)``.
    """
    project_name = project_dir.name
    block = _ORCHARD_BLOCK.format(project_name=project_name)

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
