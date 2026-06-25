# Orchard M5: SwiftUI View Tree + Navigation Flow

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** M5 milestone — SwiftUI derivation phase producing ViewTree and NavigationFlow edges from Symbol patterns, plus 2 new MCP handlers.

**Architecture:** `derive/swiftui.py` scans Symbol nodes for SwiftUI View protocol conformances, body properties, and NavigationLink/List references, writing derived edges with confidence < 1.0. `get_view_tree` and `find_navigation_flow` handlers expose these.

**Tech Stack:** Python≥3.12 + uv + Ladybug. No new dependencies.

## Global Constraints
- Python≥3.12. Ladybug `.get_all()`. Composite key `"{target_id}:{usr}"`.
- All derived edges: `derived_from` annotation, `confidence < 1.0`.
- Every MCP tool response: freshness/build_id/evidence_sources/open_gaps.
- `git add` specific files ONLY. `uv run pytest -x -q`.

## Existing Foundation (M0-M4, 103 tests, 9 MCP tools)

### Task M5-1: swiftui_derivation Phase

**Files:** Create `src/orchard/derive/swiftui.py`. Test: `tests/test_derive/test_swiftui.py`.

Add ViewTree + NavigationFlow edges to schema. Scan Symbols for SwiftUI View conformances.

### Task M5-2: get_view_tree + find_navigation_flow Handlers

**Files:** Create `src/orchard/mcp/handlers/view_tree.py`, `src/orchard/mcp/handlers/navigation_flow.py`. Test both.

Two simple handlers querying the new relation tables.

### Task M5-3: Pipeline + MCP Wiring + Acceptance

Wire phase into pipeline, register 2 new tools, write acceptance test.
