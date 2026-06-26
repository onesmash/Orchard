"""``orchard setup`` — one-shot configuration for Claude Code.

Configures the MCP server entry and installs the orchard skill.
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
    ns = ap.parse_args(args)

    # When no flags are given, install everything.
    all_items = not (ns.mcp or ns.skill or ns.model)

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
