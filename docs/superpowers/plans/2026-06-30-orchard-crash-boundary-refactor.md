# Orchard Crash Boundary Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Orchard's crash-thread analyzer surface and keep only deterministic single-frame symbol graph enrichment.

**Architecture:** Delete `orchard_lookup_crash_thread` from MCP and implementation code, then narrow `orchard_lookup_frame` so it refuses multi-line inputs instead of selecting a frame. Update generated agent guidance and the Orchard skill so agents route full crashlog understanding outside Orchard and pass only explicit symbol identity or a single frame into Orchard.

**Tech Stack:** Python 3.12, Orchard MCP server, Ladybug/KuzuDB graph access, pytest, Markdown docs.

## Global Constraints

- Do not add a replacement crash-level Orchard tool.
- Do not parse full crashlogs, crashed-thread blocks, `Application Specific Information`, exception reasons, ARM64 registers, or UIKit delegate semantics in Orchard.
- `orchard_lookup_frame` accepts exactly one frame-like symbol text.
- Multi-line input to `lookup_frame` must fail with `diag: ["input_too_broad"]`.
- Remove root-cause-ish language from README, `_ORCHARD_BLOCK`, and `skills/orchard/SKILL.md`.
- Before editing any existing function, class, method, or constant-like guidance block, run GitNexus impact analysis for the symbol and stop for user confirmation if risk is HIGH or CRITICAL.
- Before finishing implementation, run `gitnexus_detect_changes()` or the GitNexus `detect_changes` MCP tool.

---

## File Structure

- Modify `src/orchard/server.py`
  Removes the public MCP tool, dispatcher, handler function, and import for `lookup_crash_thread`. Keeps `orchard_lookup_frame` with a single-frame description.

- Modify `src/orchard/query/frame_lookup.py`
  Removes thread-level aggregation, register interpretation, and multi-line frame selection helpers. Adds a small single-frame guard used by `parse_frame_text` and `lookup_frame`.

- Modify `tests/test_mcp/test_lookup_frame.py`
  Changes MCP registration tests to assert crash-thread removal and frame-only wording.

- Modify `tests/test_mcp/test_freshness_fix.py`
  Removes the crash-thread freshness test. Search freshness coverage remains.

- Modify `tests/test_query/test_frame_lookup.py`
  Removes crash-thread tests and adds multi-line rejection tests for `parse_frame_text` / `lookup_frame`.

- Modify `tests/test_setup_block.py`
  Converts positive crash-thread assertions into guard assertions against removed crash analyzer language.

- Create `tests/test_docs/test_orchard_skill_boundary.py`
  Adds text guards for `skills/orchard/SKILL.md` so the skill cannot reintroduce crash-thread triage or register/root-cause language.

- Modify `src/orchard/setup.py`
  Updates `_ORCHARD_BLOCK` to describe Orchard as graph enrichment, not crash triage.

- Modify `skills/orchard/SKILL.md`
  Updates skill trigger guidance and crash-frame section to a single-frame-only boundary.

- Modify `README.md`
  Removes `orchard_lookup_crash_thread` from guided search and MCP tool documentation.

---

### Task 1: Remove `orchard_lookup_crash_thread` From MCP Surface

**Files:**
- Modify: `tests/test_mcp/test_lookup_frame.py`
- Modify: `tests/test_mcp/test_freshness_fix.py`
- Modify: `src/orchard/server.py`

**Interfaces:**
- Consumes: existing `orchard.server.TOOLS`, `orchard.server.HANDLERS`, and `_do_lookup_frame(args: dict) -> str`
- Produces: MCP server without `orchard_lookup_crash_thread`; `orchard_lookup_frame` remains registered

- [ ] **Step 1: Write the failing MCP tool removal test**

Replace `tests/test_mcp/test_lookup_frame.py` with:

```python
def test_lookup_frame_tool_is_registered_without_crash_thread_tool():
    import orchard.server as server_mod

    names = [tool.name for tool in server_mod.TOOLS]
    assert "orchard_lookup_frame" in names
    assert "orchard_lookup_crash_thread" not in names
    assert "orchard_lookup_crash_thread" not in server_mod.HANDLERS


def test_search_tool_description_mentions_next_actions_and_frame_lookup():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_search")
    assert "next" in tool.description.lower()
    assert "orchard_lookup_frame" in tool.description


def test_lookup_frame_tool_description_is_single_frame_only():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_lookup_frame")
    description = tool.description.lower()
    assert "single" in description
    assert "frame" in description
    assert "crashed thread" not in description
    assert "business symbol" not in description
    assert "root cause" not in description
```

- [ ] **Step 2: Remove the crash-thread freshness test**

In `tests/test_mcp/test_freshness_fix.py`, delete the entire function named
`test_lookup_crash_thread_with_build_snapshot_reports_non_unknown_freshness`.

Leave `test_search_with_build_snapshot_reports_non_unknown_freshness` and `test_plan_search_next_actions_emits_refresh_contract_before_shell_fallback` unchanged.

- [ ] **Step 3: Run tests to verify the MCP removal test fails**

Run:

```bash
uv run pytest -q tests/test_mcp/test_lookup_frame.py tests/test_mcp/test_freshness_fix.py
```

Expected: FAIL because `orchard_lookup_crash_thread` is still present in `TOOLS` / `HANDLERS`, and the frame tool description is not single-frame-only.

- [ ] **Step 4: Run impact analysis before editing server symbols**

Use GitNexus impact analysis before editing `src/orchard/server.py`:

```text
mcp__gitnexus.impact(repo="orchard2", target="_do_lookup_crash_thread", direction="upstream")
mcp__gitnexus.impact(repo="orchard2", target="TOOLS", direction="upstream")
mcp__gitnexus.impact(repo="orchard2", target="HANDLERS", direction="upstream")
```

Report the blast radius. If any result is HIGH or CRITICAL, stop and ask the user before continuing.

- [ ] **Step 5: Remove crash-thread MCP code**

In `src/orchard/server.py`, make these exact changes:

1. Change the import:

```python
from orchard.query.frame_lookup import lookup_frame
```

2. Replace the `orchard_lookup_frame` tool description with:

```python
description="Resolve a single frame-like symbol text into indexed graph context. Does not parse full crashlogs or crashed-thread blocks.",
```

3. Delete the entire `Tool(...)` block whose `name` is `orchard_lookup_crash_thread`.

4. Delete the entire handler function:

```python
def _do_lookup_crash_thread(args: dict) -> str:
    """Lookup a crashed thread and summarize indexed frames."""
    conn = _get_conn()
    freshness = _search_freshness_for_args(conn, args)
    result = lookup_crash_thread(
        conn,
        args.get("thread", ""),
        target=args.get("target", ""),
        language=args.get("language", ""),
        limit=args.get("limit", 12),
        freshness=freshness,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)
```

5. Remove this entry from `HANDLERS`:

```python
"orchard_lookup_crash_thread": _do_lookup_crash_thread,
```

- [ ] **Step 6: Run tests to verify Task 1 passes**

Run:

```bash
uv run pytest -q tests/test_mcp/test_lookup_frame.py tests/test_mcp/test_freshness_fix.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add src/orchard/server.py tests/test_mcp/test_lookup_frame.py tests/test_mcp/test_freshness_fix.py
git commit -m "refactor: remove crash thread mcp tool"
```

---

### Task 2: Narrow Frame Lookup to Single-Frame Evidence

**Files:**
- Modify: `tests/test_query/test_frame_lookup.py`
- Modify: `src/orchard/query/frame_lookup.py`

**Interfaces:**
- Consumes: `parse_frame_text(raw: str) -> dict[str, str] | None`; `lookup_frame(conn, raw: str, target: str = "", language: str = "", freshness: str = "unknown") -> dict[str, object]`
- Produces: `parse_frame_text` returns `None` for multi-line input; `lookup_frame` returns parse failure with `diag: ["input_too_broad"]` for multi-line input; no `lookup_crash_thread`

- [ ] **Step 1: Update frame lookup tests**

In `tests/test_query/test_frame_lookup.py`, change the import to:

```python
from orchard.query.frame_lookup import lookup_frame, parse_frame_text
```

Delete these test functions entirely:

- `test_lookup_frame_explains_hidden_caller_mismatch`
- `test_lookup_crash_thread_summarizes_first_indexed_symbol_and_dispatch`
- `test_lookup_crash_thread_prefers_exact_owner_when_only_constructor_owner_is_found`
- `test_lookup_crash_thread_explains_arm64_null_this_for_cxx_top_frame`

Add these tests after `test_parse_frame_text_extracts_swift_owner_and_symbol`:

```python
def test_parse_frame_text_rejects_multi_line_input():
    raw = (
        "Thread 41 Crashed:\n"
        "0 Zoom ns::Owner::crashHere() + 0\n"
        "1 Zoom ssb::thread_wrapper_t::process_msg(unsigned int)"
    )

    assert parse_frame_text(raw) is None


def test_lookup_frame_rejects_multi_line_input(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)

    result = lookup_frame(
        conn,
        "Thread 41 Crashed:\n0 Zoom ns::Owner::crashHere() + 0",
    )

    assert result["status"]["outcome"] == "parse_failed"
    assert result["status"]["coverage"] == "unknown"
    assert result["diag"] == ["input_too_broad"]
    assert result["matches"] == []
    assert result["candidates"]["frames"] == []
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_query/test_frame_lookup.py
```

Expected: FAIL because `parse_frame_text` currently scans multi-line input and returns the first parseable frame, and `lookup_crash_thread` still exists in `frame_lookup.py`.

- [ ] **Step 3: Run impact analysis before editing frame lookup symbols**

Use GitNexus impact analysis before editing `src/orchard/query/frame_lookup.py`:

```text
mcp__gitnexus.impact(repo="orchard2", target="parse_frame_text", direction="upstream")
mcp__gitnexus.impact(repo="orchard2", target="lookup_frame", direction="upstream")
mcp__gitnexus.impact(repo="orchard2", target="lookup_crash_thread", direction="upstream")
```

Report the blast radius. If any result is HIGH or CRITICAL, stop and ask the user before continuing.

- [ ] **Step 4: Add a single-frame guard and remove crash-thread implementation**

In `src/orchard/query/frame_lookup.py`, make these changes:

1. Replace the module docstring with:

```python
"""Single-frame lookup helpers for deterministic symbol graph enrichment."""
```

2. Delete `_ARM64_REGISTER_RE`.

3. Add this helper after `_FRAME_RES`:

```python
def _is_multi_line_input(raw: str) -> bool:
    return len([line for line in raw.splitlines() if line.strip()]) > 1
```

4. Replace `parse_frame_text` with:

```python
def parse_frame_text(raw: str) -> dict[str, str] | None:
    """Extract owner/symbol/signature from one frame-like text string."""
    if _is_multi_line_input(raw):
        return None
    return _parse_frame_line(raw.strip())
```

5. Add this branch at the start of `lookup_frame`, before `parsed = parse_frame_text(raw)`:

```python
    if _is_multi_line_input(raw):
        return SearchResponse(
            query={"raw": raw, "kind": "frame"},
            status=SearchStatus(
                outcome="parse_failed", coverage="unknown", freshness=freshness
            ),
            matches=[],
            diag=["input_too_broad"],
            candidates={"symbols": [], "owners": [], "text": [], "frames": []},
            next_actions=[],
        ).to_dict()
```

6. Delete the entire `lookup_crash_thread` function.

7. Delete these helper functions because they only support thread-level or
   register-level interpretation:

- `_annotate_parsed_boundary`
- `_register_semantics`
- `_parse_arm64_registers`
- `_extract_frame_lines`
- `_dedupe`

- [ ] **Step 5: Run tests to verify Task 2 passes**

Run:

```bash
uv run pytest -q tests/test_query/test_frame_lookup.py tests/test_mcp/test_lookup_frame.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add src/orchard/query/frame_lookup.py tests/test_query/test_frame_lookup.py
git commit -m "refactor: keep frame lookup single frame only"
```

---

### Task 3: Update Agent Guidance, README, and Guard Tests

**Files:**
- Modify: `tests/test_setup_block.py`
- Create: `tests/test_docs/test_orchard_skill_boundary.py`
- Modify: `src/orchard/setup.py`
- Modify: `skills/orchard/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `_ORCHARD_BLOCK` formatted with `project_name`, `symbol_count`, `calls_count`, `contains_count`
- Produces: docs and generated guidance that describe Orchard as graph enrichment, not crashlog/crashed-thread analysis

- [ ] **Step 1: Write failing `_ORCHARD_BLOCK` guard tests**

Replace `tests/test_setup_block.py` with:

```python
from orchard.setup import _ORCHARD_BLOCK


def _render_block() -> str:
    return _ORCHARD_BLOCK.format(
        project_name="Demo",
        symbol_count=1,
        calls_count=2,
        contains_count=3,
    )


def test_orchard_block_mentions_single_frame_boundary():
    block = _render_block()

    assert "orchard_lookup_frame" in block
    assert "single stack frame" in block
    assert "full crashlogs are handled outside Orchard" in block
    assert "explicit symbol identity" in block
    assert len(block.splitlines()) <= 90


def test_orchard_block_excludes_crash_thread_analyzer_language():
    block = _render_block().lower()

    forbidden = [
        "orchard_lookup_crash_thread",
        "crashed-thread",
        "crashed thread",
        "crash triage",
        "first indexed business symbol",
        "business_first_frame",
        "thread/dispatch boundaries",
        "dispatch_boundaries",
        "arm64",
        "x0 = 0",
        "arm64_null_this",
        "likely_fault",
        "root_cause",
        "delegate selector inference",
    ]
    for text in forbidden:
        assert text not in block


def test_orchard_block_keeps_graph_context_labels():
    block = _render_block()

    assert "call_style" in block
    assert "execution_boundary" in block
    assert "source_scope" in block
    assert "outside_workspace_root" in block
    assert "data.summary" in block
    assert "exact C++ object field offsets" in block
    assert "orchard_class_layout" not in block
    assert "## Graph Schema" not in block
```

- [ ] **Step 2: Add failing Orchard skill boundary tests**

Create `tests/test_docs/test_orchard_skill_boundary.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "orchard" / "SKILL.md"


def test_orchard_skill_describes_single_frame_graph_enrichment():
    text = SKILL.read_text()

    assert "orchard_lookup_frame" in text
    assert "single stack frame" in text
    assert "full crashlogs are handled outside Orchard" in text
    assert "explicit symbol identity" in text


def test_orchard_skill_excludes_crash_analyzer_language():
    text = SKILL.read_text().lower()

    forbidden = [
        "orchard_lookup_crash_thread",
        "crashed-thread triage",
        "crashed thread",
        "arm64 register clues",
        "x0 = 0",
        "arm64_null_this",
        "likely_fault",
        "root_cause",
        "delegate selector inference",
        "business_first_frame",
        "thread_boundaries",
        "dispatch_boundaries",
    ]
    for phrase in forbidden:
        assert phrase not in text
```

- [ ] **Step 3: Run docs tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_setup_block.py tests/test_docs/test_orchard_skill_boundary.py
```

Expected: FAIL because `_ORCHARD_BLOCK` and `skills/orchard/SKILL.md` still describe crash-thread triage, register clues, and `orchard_lookup_crash_thread`.

- [ ] **Step 4: Run impact analysis before editing guidance block**

Use GitNexus impact analysis before editing `_ORCHARD_BLOCK`:

```text
mcp__gitnexus.impact(repo="orchard2", target="_ORCHARD_BLOCK", direction="upstream")
```

Report the blast radius. If risk is HIGH or CRITICAL, stop and ask the user before continuing.

- [ ] **Step 5: Update `_ORCHARD_BLOCK` in `src/orchard/setup.py`**

In `src/orchard/setup.py`, update `_ORCHARD_BLOCK` with these content changes:

1. Replace the intro sentence:

```markdown
This project is indexed by orchard as **{project_name}** ({symbol_count:,} symbols, {calls_count:,} calls, {contains_count:,} contains). Use Orchard MCP tools for compiler-indexed code navigation, deterministic symbol graph enrichment, and impact analysis.
```

2. Replace the Debugging Flow with:

```markdown
## Debugging Flow

1. `orchard_search({{name: "<symbol>"}})` — guided symbol lookup with `status`, `diag`, `candidates`, and `next`
2. If the user has a single stack frame, use `orchard_lookup_frame({{frame: "<stack line>"}})` to resolve owner/method candidates and graph context
3. If the user pasted a full crashlog or crashed-thread block, extract a concrete frame, symbol name, qualified name, or USR outside Orchard first. Full crashlogs are handled outside Orchard.
4. `orchard_find_callers({{usr: "<USR>"}})` — see who calls it; each entry has `confidence` (compiler-verified / inferred), `call_style`, optional `execution_boundary`, and `source_scope`
5. `orchard_find_callees({{usr: "<USR>"}})` — see what it calls; ObjC callees carry `semantic_role` (notification_observer, delegate_setter, framework_callback...) and notification_bridges (who registered → selector → event key → callback) by default
6. `orchard_impact({{usr: "<USR>"}})` — assess blast radius with depth groups plus compact `summary`
```

3. Replace `## Crash Triage Notes` with:

```markdown
## Frame Lookup Boundary

- `orchard_lookup_frame` accepts one single stack frame or frame-like symbol text.
- Full crashlogs are handled outside Orchard; pass a single frame or explicit symbol identity to Orchard.
- Caller/callee results may include `call_style: synchronous_call` or `async_or_callback_boundary`.
- `execution_boundary.role` is heuristic and helps identify SDK callbacks, worker-thread dispatch, main-thread tasks, notification/callback sinks, and lifecycle/uninit paths.
- `source_scope.status: outside_workspace_root` means the indexed symbol's source is outside the current workspace root; grep under cwd may not find it.
- Do not claim Orchard has exact C++ object field offsets from IndexStore. Treat addresses such as `0x20` as hypotheses only; exact class/member offsets require DWARF, Clang record layout output, or another ABI-aware source.
```

4. In the Tools Quick Reference table, replace the lookup rows with only:

```markdown
| `lookup_frame` | Resolve a single stack frame to owner/method candidates and graph context | `orchard_lookup_frame({{frame: "ssb::thread_wrapper_t::process_msg(unsigned int)"}})` |
```

- [ ] **Step 6: Update `skills/orchard/SKILL.md`**

Make these content changes:

1. In frontmatter description, remove:

```markdown
crashed-thread triage, ARM64 register clues such as `x0 = 0`,
```

2. In frontmatter description, keep crash-line trigger language but make it single-frame-only:

```markdown
single crash-frame lookup,
```

3. Replace the `## Crash-frame lookup` section from its heading through the paragraph ending `orchard_lookup_frame next instead of improvising.` with:

````markdown
## Single-frame lookup

Use `orchard_lookup_frame` when the user has one stack frame or one
frame-like symbol string:

```json
{"frame": "ssb::thread_wrapper_t::process_msg(unsigned int)"}
```

Use it for:

- a single stack frame
- a frame-like symbol with namespace, owner, and parameters
- "I only have this stack line, where do I start?"

Full crashlogs are handled outside Orchard. If the user pasted a full crashlog
or crashed-thread block, first extract a concrete frame, symbol name, qualified
name, or USR outside Orchard, then call `orchard_lookup_frame`,
`orchard_search`, or a USR-based graph tool.

Do **not** ask Orchard to choose the first business frame, interpret exception
sections, infer delegate selectors, rank likely causes, or inspect register
values. Orchard enriches explicit symbol identity with compiler-indexed graph
context.

If `orchard_search` returns `frame_lookup_recommended`, call
`orchard_lookup_frame` next only when the input is one frame-like string.
````

4. In the MCP tools list near the bottom, remove `orchard_lookup_crash_thread` so it reads:

```markdown
MCP tools: `orchard_search`, `orchard_lookup_frame`, `orchard_find_callers`, `orchard_find_callees`
```

- [ ] **Step 7: Update `README.md`**

Make these content changes:

1. In Guided Search, replace the frame bullets with:

```markdown
- Use `orchard_lookup_frame` when you have one stack frame or frame-like symbol text and want Orchard to resolve owner/method graph context.
- Full crashlogs and crashed-thread blocks are handled outside Orchard. Extract a concrete frame, symbol name, qualified name, or USR before calling Orchard.
```

2. In the MCP tools table, remove the `orchard_lookup_crash_thread` row.

3. Replace the `orchard_lookup_frame` row description with:

```markdown
| `orchard_lookup_frame` | Resolve a single stack frame to owner/method graph context |
```

- [ ] **Step 8: Run docs tests to verify Task 3 passes**

Run:

```bash
uv run pytest -q tests/test_setup_block.py tests/test_docs/test_orchard_skill_boundary.py
```

Expected: PASS.

- [ ] **Step 9: Commit Task 3**

Run:

```bash
git add src/orchard/setup.py skills/orchard/SKILL.md README.md tests/test_setup_block.py tests/test_docs/test_orchard_skill_boundary.py
git commit -m "docs: clarify orchard frame lookup boundary"
```

---

### Task 4: Final Regression And Scope Verification

**Files:**
- No planned source files beyond previous tasks

**Interfaces:**
- Consumes: all changes from Tasks 1-3
- Produces: verified implementation with no crash-thread public surface

- [ ] **Step 1: Search for forbidden public-surface terms**

Run:

```bash
rg -n "orchard_lookup_crash_thread|lookup_crash_thread|business_first_frame|first indexed business symbol|likely_fault|arm64_null_this|x0 = 0|crashed-thread triage|delegate selector inference|root_cause" src tests README.md skills/orchard/SKILL.md docs/superpowers/specs/2026-06-30-orchard-crash-boundary-refactor-design.md
```

Expected: matches only inside the design spec or in test guard strings that
assert absence. There should be no functional-code matches in `src`, and no
content matches in `README.md` or `skills/orchard/SKILL.md`.

- [ ] **Step 2: Run targeted tests**

Run:

```bash
uv run pytest -q \
  tests/test_query/test_frame_lookup.py \
  tests/test_mcp/test_lookup_frame.py \
  tests/test_mcp/test_freshness_fix.py \
  tests/test_setup_block.py \
  tests/test_docs/test_orchard_skill_boundary.py
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 4: Run GitNexus change detection**

Use GitNexus detect changes:

```text
mcp__gitnexus.detect_changes(repo="orchard2", scope="all")
```

Expected: changed symbols and affected flows match the crash-boundary refactor: server tool removal, frame lookup single-line guard, setup guidance, docs, and tests.

- [ ] **Step 5: Commit final verification fixes if any**

If Step 1-4 required small fixes, commit them:

```bash
git add src/orchard/server.py src/orchard/query/frame_lookup.py src/orchard/setup.py skills/orchard/SKILL.md README.md tests
git commit -m "test: guard orchard crash boundary"
```

If no files changed after Task 3, do not create an empty commit.

- [ ] **Step 6: Report implementation summary**

Report:

- `orchard_lookup_crash_thread` removed from MCP and handlers
- `lookup_frame` rejects multi-line input
- `_ORCHARD_BLOCK`, `skills/orchard/SKILL.md`, and README no longer describe Orchard as a crash-thread analyzer
- tests run and results
- GitNexus detect-changes scope and risk summary
