# Orchard Feedback Guided Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Orchard search so miss-path responses are diagnosable, compact, and agent-actionable, and add frame-oriented lookup for crash-debugging workflows.

**Architecture:** Keep the current MCP server entrypoints in `src/orchard/server.py`, but move the new response contract, ranking logic, and frame-lookup fallback logic into focused query/handler helpers. Reuse existing build snapshot freshness primitives and graph lookups, then layer compact `status`, `diag`, `candidates`, and `next` planning on top without introducing a new general-purpose resolver or a new phase-1 refresh MCP tool.

**Tech Stack:** Python 3.12, Orchard MCP server, Ladybug graph DB, pytest

## Global Constraints

- Full crashlog semantic analysis is out of scope for phase 1.
- Rebuilding C++ symbol indexing quality end to end is out of scope for phase 1.
- Long natural-language explanations in default MCP responses are out of scope for phase 1.
- Backward compatibility with the current `orchard_search` response shape is out of scope for phase 1.
- `orchard_search` is for symbol-intent lookup.
- `orchard_lookup_frame` is for translating stack-frame text into an executable search path.
- Default responses must optimize for information density, not prose.
- Freshness and degraded-mode signals must be surfaced explicitly instead of being hidden behind empty results.
- Phase 1 `next` actions may only be Orchard MCP tool calls, Orchard maintenance actions, or explicit shell fallback actions.
- `orchard_refresh_index` is a maintenance action contract in phase 1, not a new Orchard MCP tool.
- Phase 1 candidate ordering must use only cheap, existing signals; do not build a GitNexus-style general-purpose scope resolver.
- Keep TDD strict: each task starts red, goes green with minimal code, then commits.

---

## File Structure

- Create: `src/orchard/query/search_contract.py`
  Purpose: Define compact search response helpers for `status`, `diag`, `candidates`, and `next`, plus JSON-friendly serialization helpers.
- Create: `src/orchard/query/search_planner.py`
  Purpose: Classify `orchard_search` input, rank candidates deterministically, compute phase-1 `next` actions, and keep the miss-path logic out of `server.py`.
- Create: `src/orchard/query/frame_lookup.py`
  Purpose: Parse stack-frame-like input, generate layered fallbacks, and return the shared search contract shape.
- Modify: `src/orchard/server.py`
  Purpose: Register `orchard_lookup_frame`, update tool descriptions, and route `orchard_search` through the new planner.
- Modify: `src/orchard/validation/freshness.py`
  Purpose: Add a compact freshness mapping for search responses without changing the existing handler compatibility contract.
- Modify: `src/orchard/cli.py`
  Purpose: Expose or reuse the exact refresh command string used by the maintenance action contract.
- Create: `tests/test_query/test_search_contract.py`
  Purpose: Pin the shared contract shape and deterministic `next` action serialization.
- Create: `tests/test_query/test_search_planner.py`
  Purpose: Pin query classification, candidate ranking, and shell/maintenance action decisions.
- Create: `tests/test_query/test_frame_lookup.py`
  Purpose: Pin frame parsing and fallback sequencing.
- Modify: `tests/test_mcp/test_search_by_kind.py`
  Purpose: Replace legacy search-shape assertions with the new search response contract and deterministic ranking checks.
- Create: `tests/test_mcp/test_lookup_frame.py`
  Purpose: Validate the new MCP tool end-to-end through `server.py`.
- Create: `tests/test_validation/test_search_freshness_status.py`
  Purpose: Pin the phase-1 freshness mapping order independently from the older `freshness_for(...)` API.

### Task 1: Add the shared search response contract

**Files:**
- Create: `src/orchard/query/search_contract.py`
- Create: `tests/test_query/test_search_contract.py`

**Interfaces:**
- Consumes: no new runtime dependencies
- Produces:
  - `SearchStatus(outcome: str, coverage: str, freshness: str) -> SearchStatus`
  - `SearchResponse(query: dict, status: SearchStatus, matches: list[dict], diag: list[str], candidates: dict[str, list], next_actions: list[dict]) -> SearchResponse`
  - `SearchResponse.to_dict() -> dict[str, object]`

- [ ] **Step 1: Write the failing tests**

```python
from orchard.query.search_contract import SearchResponse, SearchStatus


def test_search_response_to_dict_keeps_compact_keys():
    resp = SearchResponse(
        query={"raw": "process_msg", "kind": "symbol"},
        status=SearchStatus(outcome="no_match", coverage="partial", freshness="stale"),
        matches=[],
        diag=["index_stale", "owner_search_recommended"],
        candidates={"symbols": [], "owners": ["thread_wrapper_t"], "text": ["process_msg"]},
        next_actions=[
            {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
            {"tool": "shell_text_search", "args": {"pattern": "process_msg"}},
        ],
    )

    assert resp.to_dict() == {
        "query": {"raw": "process_msg", "kind": "symbol"},
        "status": {
            "outcome": "no_match",
            "coverage": "partial",
            "freshness": "stale",
        },
        "matches": [],
        "diag": ["index_stale", "owner_search_recommended"],
        "candidates": {
            "symbols": [],
            "owners": ["thread_wrapper_t"],
            "text": ["process_msg"],
        },
        "next": [
            {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
            {"tool": "shell_text_search", "args": {"pattern": "process_msg"}},
        ],
    }


def test_search_response_rejects_unknown_freshness_value():
    try:
        SearchStatus(outcome="no_match", coverage="partial", freshness="broken")
    except ValueError as exc:
        assert "freshness" in str(exc)
    else:
        raise AssertionError("SearchStatus should reject unsupported freshness values")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_query/test_search_contract.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'orchard.query.search_contract'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass

_OUTCOMES = {"match", "ambiguous", "near_match", "no_match", "parse_failed"}
_COVERAGE = {"covered", "partial", "uncovered", "unknown"}
_FRESHNESS = {"fresh", "stale", "partially_stale", "unknown"}


@dataclass(frozen=True)
class SearchStatus:
    outcome: str
    coverage: str
    freshness: str

    def __post_init__(self):
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"unsupported outcome: {self.outcome}")
        if self.coverage not in _COVERAGE:
            raise ValueError(f"unsupported coverage: {self.coverage}")
        if self.freshness not in _FRESHNESS:
            raise ValueError(f"unsupported freshness: {self.freshness}")


@dataclass(frozen=True)
class SearchResponse:
    query: dict
    status: SearchStatus
    matches: list[dict]
    diag: list[str]
    candidates: dict[str, list]
    next_actions: list[dict]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "status": {
                "outcome": self.status.outcome,
                "coverage": self.status.coverage,
                "freshness": self.status.freshness,
            },
            "matches": self.matches,
            "diag": self.diag,
            "candidates": self.candidates,
            "next": self.next_actions,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_query/test_search_contract.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/query/search_contract.py tests/test_query/test_search_contract.py
git commit -m "feat: add compact search response contract"
```

### Task 2: Add phase-1 freshness mapping and deterministic search planning

**Files:**
- Create: `src/orchard/query/search_planner.py`
- Modify: `src/orchard/validation/freshness.py`
- Create: `tests/test_query/test_search_planner.py`
- Create: `tests/test_validation/test_search_freshness_status.py`

**Interfaces:**
- Consumes:
  - `freshness_for(conn, build_id: str, query_ctx: dict) -> tuple[GraphFreshness, str]`
  - `SearchStatus`
  - raw symbol rows shaped as `{"usr": str, "name": str, "kind": str, "language": str, "module": str}`
- Produces:
  - `map_search_freshness(snapshot_status: str) -> str`
  - `classify_search_query(raw: str) -> str`
  - `rank_symbol_candidates(raw: str, rows: list[dict], target: str = "", language: str = "") -> list[dict]`
  - `plan_search_next_actions(status: SearchStatus, candidates: dict[str, list], raw: str) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
from orchard.query.search_planner import (
    classify_search_query,
    plan_search_next_actions,
    rank_symbol_candidates,
)
from orchard.query.search_contract import SearchStatus
from orchard.validation.freshness import map_search_freshness


def test_map_search_freshness_keeps_phase1_values_small():
    assert map_search_freshness("fresh") == "fresh"
    assert map_search_freshness("stale") == "stale"
    assert map_search_freshness("toolchain_mismatch") == "partially_stale"
    assert map_search_freshness("build_mismatch") == "partially_stale"


def test_classify_search_query_distinguishes_frame_like_input():
    assert classify_search_query("process_msg") == "symbol"
    assert classify_search_query("ssb::thread_wrapper_t") == "qualified_symbol"
    assert classify_search_query("ssb::thread_wrapper_t::process_msg(unsigned int)") == "frame"


def test_rank_symbol_candidates_prefers_owner_and_case_preserving_matches():
    rows = [
        {"usr": "u1", "name": "Process_Msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u2", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u3", "name": "process_msgLater", "kind": "cxx.method", "language": "cxx", "module": "Core"},
    ]
    ranked = rank_symbol_candidates("process_msg", rows)
    assert [row["usr"] for row in ranked] == ["u2", "u1", "u3"]


def test_plan_search_next_actions_prefers_refresh_before_shell_fallback():
    status = SearchStatus(outcome="no_match", coverage="partial", freshness="stale")
    next_actions = plan_search_next_actions(
        status,
        {"symbols": [], "owners": ["thread_wrapper_t"], "text": ["process_msg"]},
        "process_msg",
    )
    assert next_actions[0]["tool"] == "orchard_refresh_index"
    assert next_actions[1]["tool"] == "orchard_search"
    assert next_actions[2]["tool"] == "shell_text_search"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_query/test_search_planner.py tests/test_validation/test_search_freshness_status.py`

Expected: FAIL with missing function or import errors for `map_search_freshness`, `classify_search_query`, `rank_symbol_candidates`, or `plan_search_next_actions`

- [ ] **Step 3: Write minimal implementation**

```python
def map_search_freshness(snapshot_status: str) -> str:
    if snapshot_status == "fresh":
        return "fresh"
    if snapshot_status == "stale":
        return "stale"
    if snapshot_status in {"toolchain_mismatch", "build_mismatch"}:
        return "partially_stale"
    return "unknown"
```

```python
def classify_search_query(raw: str) -> str:
    if "::" in raw and "(" in raw and ")" in raw:
        return "frame"
    if "::" in raw:
        return "qualified_symbol"
    return "symbol"


def _candidate_key(raw: str, row: dict, target: str = "", language: str = "") -> tuple:
    name = row["name"]
    return (
        0 if name == raw and "::" in raw else 1,
        0 if name == raw else 1,
        0 if name == raw else 1 if name.lower() == raw.lower() else 2,
        0 if target and row.get("module") == target else 1,
        0 if language and row.get("language") == language else 1,
        0 if name.startswith(raw) else 1,
        row.get("module", ""),
        row.get("kind", ""),
        name,
        row.get("usr", ""),
    )


def rank_symbol_candidates(raw: str, rows: list[dict], target: str = "", language: str = "") -> list[dict]:
    return sorted(rows, key=lambda row: _candidate_key(raw, row, target, language))


def plan_search_next_actions(status, candidates: dict[str, list], raw: str) -> list[dict]:
    actions: list[dict] = []
    if status.freshness in {"stale", "unknown"}:
        actions.append({"tool": "orchard_refresh_index", "args": {}})
    for owner in candidates.get("owners", [])[:1]:
        actions.append({"tool": "orchard_search", "args": {"name": owner}})
    if candidates.get("text"):
        actions.append({"tool": "shell_text_search", "args": {"pattern": raw}})
    return actions[:3]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_query/test_search_planner.py tests/test_validation/test_search_freshness_status.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/query/search_planner.py src/orchard/validation/freshness.py tests/test_query/test_search_planner.py tests/test_validation/test_search_freshness_status.py
git commit -m "feat: add search freshness mapping and planning"
```

### Task 3: Replace `orchard_search` output with the new search contract

**Files:**
- Modify: `src/orchard/server.py`
- Modify: `tests/test_mcp/test_search_by_kind.py`

**Interfaces:**
- Consumes:
  - `classify_search_query(raw: str) -> str`
  - `rank_symbol_candidates(raw: str, rows: list[dict], target: str = "", language: str = "") -> list[dict]`
  - `plan_search_next_actions(status: SearchStatus, candidates: dict[str, list], raw: str) -> list[dict]`
  - `map_search_freshness(snapshot_status: str) -> str`
- Produces:
  - `_do_search_name(args: dict) -> str` returning the new compact response JSON
  - tool description for `orchard_search` updated to mention `orchard_lookup_frame`, freshness, and `next`

- [ ] **Step 1: Write the failing tests**

```python
def test_search_name_returns_compact_status_and_next(conn_with_mixed_symbols):
    import json
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols
    try:
        result = json.loads(server_mod._do_search_name({"name": "toolbarItems"}))
        assert result["query"]["kind"] == "symbol"
        assert result["status"]["outcome"] == "ambiguous"
        assert result["status"]["coverage"] == "covered"
        assert "freshness" in result["status"]
        assert "matches" in result
        assert "next" in result
    finally:
        server_mod._conn = original_conn


def test_search_name_no_match_prefers_shell_text_search_after_owner_hint(conn_with_mixed_symbols):
    import json
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols
    try:
        result = json.loads(server_mod._do_search_name({"name": "process_msg"}))
        assert result["status"]["outcome"] in {"no_match", "near_match"}
        assert result["next"][-1]["tool"] == "shell_text_search"
    finally:
        server_mod._conn = original_conn


def test_search_name_frame_like_input_routes_to_lookup_frame(conn_with_mixed_symbols):
    import json
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols
    try:
        result = json.loads(
            server_mod._do_search_name({"name": "ssb::thread_wrapper_t::process_msg(unsigned int)"})
        )
        assert "frame_lookup_recommended" in result["diag"]
        assert result["next"][0]["tool"] == "orchard_lookup_frame"
    finally:
        server_mod._conn = original_conn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_mcp/test_search_by_kind.py`

Expected: FAIL because `_do_search_name` still returns legacy `count/by_kind/results` JSON without `status`, `diag`, or `next`

- [ ] **Step 3: Write minimal implementation**

```python
raw = args.get("name", "")
query_kind = classify_search_query(raw)
if query_kind == "frame":
    response = SearchResponse(
        query={"raw": raw, "kind": query_kind},
        status=SearchStatus(outcome="no_match", coverage="unknown", freshness="unknown"),
        matches=[],
        diag=["frame_lookup_recommended"],
        candidates={"symbols": [], "owners": [], "text": [raw]},
        next_actions=[{"tool": "orchard_lookup_frame", "args": {"frame": raw}}],
    )
    return json.dumps(response.to_dict(), ensure_ascii=False, indent=2)

rows = conn.execute(...).get_all()
matches = rank_symbol_candidates(raw, [
    {"usr": r[0], "name": r[1], "kind": r[2], "language": r[3], "module": r[4]}
    for r in rows
], target=target, language=language)
outcome = "match" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "no_match"
response = SearchResponse(
    query={"raw": raw, "kind": query_kind},
    status=SearchStatus(outcome=outcome, coverage="covered" if matches else "unknown", freshness="unknown"),
    matches=matches[:5],
    diag=[] if matches else ["text_fallback_recommended"],
    candidates={"symbols": matches[:3], "owners": [], "text": [raw] if not matches else []},
    next_actions=plan_search_next_actions(
        SearchStatus(outcome=outcome, coverage="covered" if matches else "unknown", freshness="unknown"),
        {"symbols": matches[:3], "owners": [], "text": [raw]},
        raw,
    ),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_mcp/test_search_by_kind.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/server.py tests/test_mcp/test_search_by_kind.py
git commit -m "feat: upgrade orchard search response contract"
```

### Task 4: Add `orchard_lookup_frame` and shared frame fallback logic

**Files:**
- Create: `src/orchard/query/frame_lookup.py`
- Modify: `src/orchard/server.py`
- Create: `tests/test_query/test_frame_lookup.py`
- Create: `tests/test_mcp/test_lookup_frame.py`

**Interfaces:**
- Consumes:
  - `SearchResponse`
  - `SearchStatus`
  - `rank_symbol_candidates(raw: str, rows: list[dict], target: str = "", language: str = "") -> list[dict]`
- Produces:
  - `parse_frame_text(raw: str) -> dict[str, str] | None`
  - `lookup_frame(conn, raw: str, target: str = "", language: str = "") -> dict[str, object]`
  - MCP tool registration: `orchard_lookup_frame`

- [ ] **Step 1: Write the failing tests**

```python
from orchard.query.frame_lookup import lookup_frame, parse_frame_text


def test_parse_frame_text_extracts_owner_and_symbol():
    parsed = parse_frame_text("ssb::thread_wrapper_t::process_msg(unsigned int)")
    assert parsed == {
        "qualified_name": "ssb::thread_wrapper_t::process_msg",
        "owner": "thread_wrapper_t",
        "symbol": "process_msg",
        "signature": "unsigned int",
    }


def test_lookup_frame_falls_back_from_qualified_to_owner(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 'u1', usr: 'u1', precise_id: '', name: 'thread_wrapper_t', "
        "language: 'cxx', kind: 'cxx.class', module: 'Core', file_path: '/src/thread.cpp', "
        "signature: '', container_usr: '', access_level: 'internal', origin: 'derived', is_generated: false})"
    )

    result = lookup_frame(conn, "ssb::thread_wrapper_t::process_msg(unsigned int)")
    assert result["status"]["outcome"] in {"near_match", "no_match"}
    assert result["candidates"]["owners"] == ["thread_wrapper_t"]
    assert result["next"][0]["tool"] == "orchard_search"
    conn.close()
```

```python
def test_lookup_frame_tool_is_registered():
    import orchard.server as server_mod

    names = [tool.name for tool in server_mod.TOOLS]
    assert "orchard_lookup_frame" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_query/test_frame_lookup.py tests/test_mcp/test_lookup_frame.py`

Expected: FAIL because `frame_lookup.py` and `orchard_lookup_frame` do not exist

- [ ] **Step 3: Write minimal implementation**

```python
def parse_frame_text(raw: str) -> dict[str, str] | None:
    if "::" not in raw or "(" not in raw or ")" not in raw:
        return None
    head, _, tail = raw.partition("(")
    signature = tail.rsplit(")", 1)[0]
    parts = head.split("::")
    if len(parts) < 2:
        return None
    return {
        "qualified_name": head,
        "owner": parts[-2],
        "symbol": parts[-1],
        "signature": signature,
    }


def lookup_frame(conn, raw: str, target: str = "", language: str = "") -> dict[str, object]:
    parsed = parse_frame_text(raw)
    if parsed is None:
        return SearchResponse(
            query={"raw": raw, "kind": "frame"},
            status=SearchStatus(outcome="parse_failed", coverage="unknown", freshness="unknown"),
            matches=[],
            diag=["frame_lookup_recommended"],
            candidates={"symbols": [], "owners": [], "text": [raw], "frames": []},
            next_actions=[{"tool": "shell_text_search", "args": {"pattern": raw}}],
        ).to_dict()
    owner_rows = conn.execute(
        "MATCH (s:Symbol) WHERE s.name = $name RETURN s.usr, s.name, s.kind, s.language, s.module LIMIT 5",
        {"name": parsed["owner"]},
    ).get_all()
    owners = [row[1] for row in owner_rows]
    return SearchResponse(
        query={"raw": raw, "kind": "frame"},
        status=SearchStatus(outcome="near_match" if owners else "no_match", coverage="partial", freshness="unknown"),
        matches=[],
        diag=[] if owners else ["frame_outside_index_scope"],
        candidates={"symbols": [], "owners": owners[:3], "text": [parsed["symbol"]], "frames": [parsed]},
        next_actions=(
            [{"tool": "orchard_search", "args": {"name": owners[0]}}] if owners else [{"tool": "shell_text_search", "args": {"pattern": parsed["symbol"]}}]
        ),
    ).to_dict()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_query/test_frame_lookup.py tests/test_mcp/test_lookup_frame.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/query/frame_lookup.py src/orchard/server.py tests/test_query/test_frame_lookup.py tests/test_mcp/test_lookup_frame.py
git commit -m "feat: add orchard frame lookup tool"
```

### Task 5: Wire freshness, maintenance actions, and degraded guidance into end-to-end search behavior

**Files:**
- Modify: `src/orchard/server.py`
- Modify: `src/orchard/cli.py`
- Modify: `tests/test_mcp/test_search_by_kind.py`
- Modify: `tests/test_mcp/test_freshness_fix.py`

**Interfaces:**
- Consumes:
  - `_default_build_id_safe(conn, scope_id: str = "") -> str | None`
  - `freshness_for(conn, build_id: str, query_ctx: dict) -> tuple[GraphFreshness, str]`
  - `map_search_freshness(snapshot_status: str) -> str`
- Produces:
  - search/status freshness derived from build snapshot state when available
  - `orchard_refresh_index` maintenance action shape reused in `next`
  - a single helper for the refresh command string reused by CLI and future integrations

- [ ] **Step 1: Write the failing tests**

```python
def test_search_name_with_build_snapshot_reports_non_unknown_freshness(tmp_db_path):
    import json
    from orchard.graph.db import get_connection, init_schema
    import orchard.server as server_mod

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', workspace_root: '/app', "
        "derived_data_path: '', index_store_path: '', toolchain_id: 'Xcode15.4', "
        "commit_sha: '', build_config_hash: 'h1', created_at: '2026-06-30', sdk: 'iphonesimulator', configuration: 'debug'})"
    )
    conn.execute(
        "CREATE (:Symbol {id: 'u1', usr: 'u1', precise_id: '', name: 'process_msg', "
        "language: 'cxx', kind: 'cxx.method', module: 'Core', file_path: '/src/thread.cpp', "
        "signature: '', container_usr: '', access_level: 'internal', origin: 'derived', is_generated: false})"
    )

    original_conn = server_mod._conn
    server_mod._conn = conn
    try:
        result = json.loads(server_mod._do_search_name({"name": "process_msg"}))
        assert result["status"]["freshness"] in {"fresh", "stale", "partially_stale"}
    finally:
        server_mod._conn = original_conn
        conn.close()


def test_plan_search_next_actions_emits_refresh_contract_before_shell_fallback():
    from orchard.query.search_planner import plan_search_next_actions
    from orchard.query.search_contract import SearchStatus

    actions = plan_search_next_actions(
        SearchStatus(outcome="no_match", coverage="unknown", freshness="stale"),
        {"symbols": [], "owners": [], "text": ["process_msg"]},
        "process_msg",
    )
    assert actions == [
        {"tool": "orchard_refresh_index", "args": {}},
        {"tool": "shell_text_search", "args": {"pattern": "process_msg"}},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_mcp/test_search_by_kind.py tests/test_mcp/test_freshness_fix.py -k "freshness or refresh_contract"`

Expected: FAIL because `orchard_search` currently hardcodes `freshness="unknown"` and the refresh contract is not exercised end-to-end

- [ ] **Step 3: Write minimal implementation**

```python
def orchard_refresh_command() -> list[str]:
    return ["orchard", "ingest", "--project-dir", os.getcwd()]
```

```python
build_id = args.get("build_id") or _default_build_id_safe(conn, target or "")
snapshot_status = "stale"
if build_id:
    _, snapshot_status = freshness_for(conn, build_id, {})
freshness = map_search_freshness(snapshot_status)
status = SearchStatus(outcome=outcome, coverage=coverage, freshness=freshness)
```

```python
if status.freshness in {"stale", "unknown"}:
    actions.append({"tool": "orchard_refresh_index", "args": {}})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_mcp/test_search_by_kind.py tests/test_mcp/test_freshness_fix.py -k "freshness or refresh_contract"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/server.py src/orchard/cli.py tests/test_mcp/test_search_by_kind.py tests/test_mcp/test_freshness_fix.py
git commit -m "feat: surface search freshness and refresh actions"
```

### Task 6: Finish the agent-facing surface and regression coverage

**Files:**
- Modify: `src/orchard/server.py`
- Modify: `README.md`
- Modify: `tests/test_mcp/test_lookup_frame.py`
- Modify: `tests/test_query/test_search_planner.py`

**Interfaces:**
- Consumes:
  - final `orchard_search` and `orchard_lookup_frame` tool descriptions
  - `orchard_refresh_index` maintenance action contract
- Produces:
  - MCP descriptions that explain when to use each tool
  - regression tests for bounded `next` length and deterministic candidate order

- [ ] **Step 1: Write the failing tests**

```python
def test_search_tool_description_mentions_next_actions_and_frame_lookup():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_search")
    assert "next" in tool.description.lower()
    assert "orchard_lookup_frame" in tool.description


def test_rank_symbol_candidates_is_stable_for_same_input():
    from orchard.query.search_planner import rank_symbol_candidates

    rows = [
        {"usr": "u2", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u1", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Alpha"},
    ]
    assert [row["usr"] for row in rank_symbol_candidates("process_msg", rows)] == ["u1", "u2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_mcp/test_lookup_frame.py tests/test_query/test_search_planner.py -k "description or stable"`

Expected: FAIL because the tool descriptions have not been updated and the final stable lexical fallback is not yet locked by test

- [ ] **Step 3: Write minimal implementation**

```python
Tool(
    name="orchard_search",
    description=(
        "Search for symbols by name or qualified name. "
        "Returns compact status, diagnostics, candidates, and next actions. "
        "If the input looks like a stack frame, use orchard_lookup_frame."
    ),
    ...
)
```

```markdown
## Guided Search

- Use `orchard_search` for symbol-intent lookup.
- Use `orchard_lookup_frame` when you have a crash frame or stack text.
- If `next` includes `orchard_refresh_index`, run the documented Orchard ingest refresh command before over-trusting a miss.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_mcp/test_lookup_frame.py tests/test_query/test_search_planner.py -k "description or stable"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/server.py README.md tests/test_mcp/test_lookup_frame.py tests/test_query/test_search_planner.py
git commit -m "docs: explain guided Orchard search workflow"
```

## Self-Review

### Spec coverage

- Unified response model: Task 1 and Task 3
- `status.freshness` plus degraded diagnostics: Task 2 and Task 5
- deterministic candidate ranking: Task 2 and Task 6
- `orchard_lookup_frame`: Task 4
- maintenance action contract for refresh: Task 2 and Task 5
- agent-facing tool descriptions and guidance: Task 6

No spec requirement is left without a task.

### Placeholder scan

- No `TBD`, `TODO`, or “implement later” placeholders remain
- Each task includes concrete tests, commands, code, and commit messages
- Each interface block declares exact produced names needed by later tasks

### Type consistency

- `SearchStatus`, `SearchResponse`, `classify_search_query`, `rank_symbol_candidates`, `plan_search_next_actions`, `parse_frame_text`, and `lookup_frame` are introduced once and reused consistently
- `orchard_refresh_index` is consistently treated as a maintenance action contract rather than a new MCP tool
- `shell_text_search` is consistently treated as the explicit shell fallback action
