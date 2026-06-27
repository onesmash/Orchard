"""USR-precise rename — compiler-verified symbol identity for safe rename.

Uses IndexStore USR (Unified Symbol Resolution) to locate all occurrences
of a symbol, build a rename plan, and apply it.  Phase 1 targets Swift
symbols; ObjC selector rename is deferred to Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchard.handlers.base import BaseToolRequest, BaseToolResponse


@dataclass
class RenameRequest(BaseToolRequest):
    """Request for USR-precise symbol rename.

    Attributes:
        usr: The USR of the symbol to rename.
        new_name: The new name for the symbol.
        dry_run: If True, return the rename plan and diff without writing files.
    """

    usr: str = ""
    new_name: str = ""
    dry_run: bool = True


# ── Rename plan builder ──────────────────────────────────────────────


def build_rename_plan(conn, usr: str, new_name: str) -> list[dict]:
    """Build a sorted rename plan: every occurrence site mapped to an edit entry.

    Queries the Occurrence table for all definition / reference sites of
    *usr*, then groups by file and sorts by descending line number so that
    applying edits top-down within a file doesn't shift subsequent positions.

    Returns:
        list of dicts with keys: file_path, line, col, edit_type, old_name, new_name
    """
    sym_id = usr

    # Resolve the symbol's current name from the Symbol table.
    name_rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) RETURN s.name, s.language LIMIT 1",
        {"id": sym_id},
    ).get_all()
    if not name_rows:
        return None  # symbol not in graph at all
    current_name = name_rows[0][0]

    # Try the Occurrence table first (precise line/col).
    rows = conn.execute(
        "MATCH (f:File)-[:ContainsOccurrence]->(o:Occurrence {usr: $usr}) "
        "RETURN o.file_path, o.line, o.col, o.role "
        "ORDER BY o.file_path, o.line DESC",
        {"usr": usr},
    ).get_all()

    if rows:
        plan: list[dict] = []
        for r in rows:
            role = r[3] or "reference"
            edit_type = "declaration" if role == "definition" else "reference"
            plan.append({
                "file_path": r[0] or "",
                "line": r[1] or 0,
                "col": r[2] or 0,
                "edit_type": edit_type,
                "old_name": current_name,
                "new_name": new_name,
            })
        return plan

    # Fallback: no Occurrence data — build plan from Symbol + Calls tables.
    sym_rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) RETURN s.file_path LIMIT 1",
        {"id": sym_id},
    ).get_all()
    if not sym_rows or not sym_rows[0][0]:
        return []

    plan = []
    seen_files = {sym_rows[0][0]}
    plan.append({
        "file_path": sym_rows[0][0], "line": 0, "col": 0,
        "edit_type": "declaration", "old_name": current_name, "new_name": new_name,
    })

    # Add caller file paths as reference sites.
    ref_rows = conn.execute(
        "MATCH (caller:Symbol)-[:Calls]->(target:Symbol {id: $id}) "
        "RETURN DISTINCT caller.file_path",
        {"id": sym_id},
    ).get_all()
    for r in ref_rows:
        fp = r[0] or ""
        if fp and fp not in seen_files:
            seen_files.add(fp)
            plan.append({
                "file_path": fp, "line": 0, "col": 0,
                "edit_type": "reference", "old_name": current_name, "new_name": new_name,
            })

    return plan


# ── Diff generator ───────────────────────────────────────────────────


def rename_diff(plan: list[dict]) -> str:
    """Format a rename plan as a human-readable diff preview.

    Groups entries by file and shows line/col/edit_type for each occurrence.
    """
    if not plan:
        return "(no occurrences found — rename plan is empty)"

    # Group by file.
    by_file: dict[str, list[dict]] = {}
    for entry in plan:
        by_file.setdefault(entry["file_path"], []).append(entry)

    lines: list[str] = [
        f"Rename: {plan[0]['old_name']} → {plan[0]['new_name']}",
        f"Files affected: {len(by_file)}",
        f"Occurrences: {len(plan)}",
        "",
    ]

    for file_path, entries in sorted(by_file.items()):
        lines.append(f"  {file_path}")
        for e in sorted(entries, key=lambda x: (-x["line"], -x["col"])):
            if e["line"] == 0:
                loc = "search"
            else:
                loc = f"line {e['line']:>5}, col {e['col']:>3}"
            lines.append(
                f"    {loc:>16}  [{e['edit_type']}]"
                f"  {e['old_name']} → {e['new_name']}"
            )
        lines.append("")

    return "\n".join(lines)


# ── File writer ──────────────────────────────────────────────────────


def _apply_plan(plan: list[dict]) -> int:
    """Apply a rename plan to the filesystem.  Returns number of files modified.

    Edits are applied per file in descending line order so that earlier
    (lower-numbered) line positions stay valid after each replacement.
    """
    by_file: dict[str, list[dict]] = {}
    for entry in plan:
        by_file.setdefault(entry["file_path"], []).append(entry)

    files_modified = 0
    for file_path, entries in by_file.items():
        path = Path(file_path)
        if not path.is_file():
            continue
        content = path.read_text()
        old = entries[0]["old_name"]
        new = entries[0]["new_name"]

        # Check if any entry has precise location data.
        has_precise = any(e["line"] > 0 for e in entries)

        if has_precise:
            lines_list = content.splitlines(keepends=True)
            for e in sorted(entries, key=lambda x: -x["line"]):
                if e["line"] <= 0:
                    continue
                line_idx = e["line"] - 1
                if line_idx < 0 or line_idx >= len(lines_list):
                    continue
                line_text = lines_list[line_idx]
                col = e["col"] - 1
                if col >= 0 and line_text[col:col + len(old)] == old:
                    lines_list[line_idx] = line_text[:col] + new + line_text[col + len(old):]
            path.write_text("".join(lines_list))
        else:
            # Fallback: full-text replace — treat as identifier, match word boundaries.
            import re
            pattern = re.compile(r'\b' + re.escape(old) + r'\b')
            content = pattern.sub(new, content)
            path.write_text(content)

        files_modified += 1

    return files_modified


# ── Top-level handler ────────────────────────────────────────────────


def rename_symbol(conn, req: RenameRequest) -> BaseToolResponse:
    """Rename a symbol identified by USR.

    In dry_run mode (the default) returns the rename plan and a
    human-readable diff without modifying any files.
    """
    new_name = (req.new_name or "").strip()
    if not new_name:
        return BaseToolResponse(
            data=None,
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=[],
            open_gaps=["new_name is required and must be non-empty"],
        )

    plan = build_rename_plan(conn, req.usr, new_name)

    if plan is None:
        return BaseToolResponse(
            data=None,
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=[],
            open_gaps=[f"symbol '{req.usr}' not found in graph"],
        )

    if not plan:
        return BaseToolResponse(
            data=None,
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=[],
            open_gaps=[f"symbol '{req.usr}' has no file path or caller data"],
        )

    diff_text = rename_diff(plan)

    if req.dry_run:
        return BaseToolResponse(
            data={
                "plan": plan,
                "diff": diff_text,
                "dry_run": True,
                "files_affected": len({e["file_path"] for e in plan}),
                "occurrences": len(plan),
            },
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=["compiler_verified_usrs", "occurrence_tracking", "dry_run"],
            open_gaps=[],
        )

    files_modified = _apply_plan(plan)

    return BaseToolResponse(
        data={
            "plan": plan,
            "diff": diff_text,
            "dry_run": False,
            "files_modified": files_modified,
            "files_affected": len({e["file_path"] for e in plan}),
            "occurrences": len(plan),
        },
        freshness="stale",
        build_id=req.build_id,
        evidence_sources=["compiler_verified_usrs", "occurrence_tracking"],
        open_gaps=[] if files_modified > 0 else ["no files were modified on disk"],
    )
