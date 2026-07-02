# Orchard Target-Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add source-backed UIKit target-action bridge support so Orchard can explain dynamic action bindings in `find_callers`, `find_callees`, `find_references`, and a new dedicated target-action graph query.

**Architecture:** Reuse Orchard's existing Objective-C semantic classification and notification derivation pipeline instead of inventing a new subsystem. Keep static caller results unchanged, enrich callee/reference results with structured target-action bridges, and expose only lightweight summaries from `find_callers` when no static callers exist.

**Tech Stack:** Python, Orchard CLI, Orchard MCP server, Kuzu-backed query layer, pytest

## Global Constraints

- Do not synthesize fake `Calls` edges from UIKit runtime dispatch to action methods.
- First-version target-action fields must come only from direct graph data or directly extractable source text: `usr`, `name`, `file_path`, `module`, `line`, `selector`, `control_event`, `callback`.
- Do not add inferred fields such as `control.name`, `target.name`, `control.class_hint`, or normalized event categories.
- `find_callers` must keep `data` limited to static callers only.
- `open_gaps` must remain explanation-oriented rather than evidence-oriented.
- `find_callers` may expose only summary-level `dynamic_binding_hints`.
- `find_callees` and `find_references` may expose detailed `target_action_bridges`.
- `orchard_target_action_graph` must provide the detailed target-action query surface.

---

### Task 1: Extract Stable Target-Action Fields During Derivation

**Files:**
- Modify: `src/orchard/derive/notification_graph.py`
- Test: `tests/test_derive/test_notification_graph.py`

**Interfaces:**
- Consumes: `classify_objc_message(selector: str) -> str`
- Produces: `build_notification_graph(...) -> dict` entries in `target_actions` with `line`, `selector`, `control_event`, `callback`

- [ ] **Step 1: Write the failing derivation tests**

```python
def test_target_action_extracts_selector_and_control_event(conn):
    graph = build_notification_graph(conn, source_root="/tmp/project")
    entry = graph["target_actions"][0]
    assert entry["selector"] == "onToggle:"
    assert entry["control_event"] == "UIControlEventValueChanged"


def test_target_action_callback_is_nullable_when_selector_cannot_resolve(conn):
    graph = build_notification_graph(conn, source_root="/tmp/project")
    entry = graph["target_actions"][0]
    assert "callback" in entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_derive/test_notification_graph.py -k target_action -v`
Expected: FAIL with missing `control_event` field assertions or missing target-action fixture coverage

- [ ] **Step 3: Add source-text control-event extraction**

```python
_CONTROL_EVENT_RE = re.compile(r"forControlEvents:\s*([A-Za-z0-9_]+)")


def parse_target_action_line(line: str) -> tuple[str | None, str | None]:
    sel_match = _SELECTOR_RE.search(line)
    event_match = _CONTROL_EVENT_RE.search(line)
    selector = sel_match.group(1) if sel_match else None
    control_event = event_match.group(1) if event_match else None
    return selector, control_event
```

- [ ] **Step 4: Enrich `pending_target_actions` entries using only stable fields**

```python
entry = {
    "usr": usr,
    "name": name,
    "module": module,
    "file_path": file_path,
    "line": line_num,
    "selector": selector,
    "control_event": control_event,
    "callback": None,
}
```

- [ ] **Step 5: Resolve callback with existing same-file selector lookup**

```python
for entry in pending_target_actions:
    selector = entry.get("selector")
    if selector:
        entry["callback"] = callback_cache.get((entry["file_path"], selector))
    target_actions.append(entry)
```

- [ ] **Step 6: Run targeted tests to verify they pass**

Run: `pytest tests/test_derive/test_notification_graph.py -k target_action -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/orchard/derive/notification_graph.py tests/test_derive/test_notification_graph.py
git commit -m "feat: derive stable target-action fields"
```

### Task 2: Surface Target-Action Bridges in Query Results

**Files:**
- Modify: `src/orchard/query/lookup.py`
- Modify: `src/orchard/handlers/callees.py`
- Modify: `src/orchard/handlers/references.py`
- Test: `tests/test_mcp/test_callers.py`
- Test: `tests/test_mcp/test_references.py`

**Interfaces:**
- Consumes: derived `target_actions` entries with `usr`, `line`, `selector`, `control_event`, `callback`
- Produces: `target_action_bridges` on target-action callee entries in `find_callees` and outgoing entries in `find_references`

- [ ] **Step 1: Write the failing bridge tests**

```python
def test_find_callees_includes_target_action_bridges(conn_with_calls):
    resp = find_callees(conn_with_calls, CalleeRequest(usr="s:registrar"))
    callee = next(item for item in resp.data if item["name"] == "addTarget:action:forControlEvents:")
    bridge = callee["target_action_bridges"][0]
    assert bridge["selector"] == "onToggle:"
    assert bridge["control_event"] == "UIControlEventValueChanged"


def test_find_references_includes_target_action_bridges(conn_with_calls):
    resp = find_references(conn_with_calls, ReferencesRequest(usr="s:registrar"))
    callee = next(item for item in resp.data["outgoing"] if item["name"] == "addTarget:action:forControlEvents:")
    assert callee["target_action_bridges"][0]["callback"]["name"] == "onToggle:"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp/test_callers.py tests/test_mcp/test_references.py -k target_action -v`
Expected: FAIL with missing `target_action_bridges`

- [ ] **Step 3: Add lookup helper for target-action bridge attachment**

```python
def _target_action_bridges_for(self, observer_usr: str) -> list[dict]:
    rows = self._conn.execute(
        "MATCH (s:TargetActionBinding) WHERE s.observer_usr = $usr "
        "RETURN s.line, s.selector, s.control_event, s.callback_usr, s.callback_name, s.callback_module",
        {"usr": observer_usr},
    ).get_all()
    bridges = []
    for row in rows:
        bridges.append({
            "line": row[0],
            "selector": row[1],
            "control_event": row[2],
            "callback": None if not row[3] else {
                "usr": row[3],
                "name": row[4],
                "module": row[5] or "",
            },
        })
    return bridges
```

- [ ] **Step 4: Attach bridges only to `semantic_role == "target_action"` callees**

```python
if entry.get("semantic_role") == "target_action":
    bridges = self._target_action_bridges_for(usr)
    if bridges:
        entry["target_action_bridges"] = bridges
```

- [ ] **Step 5: Verify handler passthrough in callee/reference responses**

Run: `pytest tests/test_mcp/test_callers.py tests/test_mcp/test_references.py -k target_action -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchard/query/lookup.py src/orchard/handlers/callees.py src/orchard/handlers/references.py tests/test_mcp/test_callers.py tests/test_mcp/test_references.py
git commit -m "feat: expose target-action bridges in query results"
```

### Task 3: Add Caller-Side Dynamic Binding Summaries

**Files:**
- Modify: `src/orchard/handlers/callers.py`
- Test: `tests/test_mcp/test_callers.py`

**Interfaces:**
- Consumes: callback USR from `CallerRequest.usr`, target-action binding records keyed by callback USR
- Produces: `dynamic_binding_hints` summaries when `data` is empty and target-action bindings exist

- [ ] **Step 1: Write the failing caller-summary test**

```python
def test_find_callers_returns_target_action_summary_when_static_callers_absent(conn_with_calls):
    req = CallerRequest(usr="c:objc(cs)ZMMyNotesToggleCell(im)onToggle:", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    assert resp.data == []
    hint = resp.dynamic_binding_hints[0]
    assert hint["kind"] == "target_action"
    assert hint["binding_count"] == 1
    assert hint["bindings"][0]["callback_name"] == "onToggle:"
```

- [ ] **Step 2: Run the caller test to verify it fails**

Run: `pytest tests/test_mcp/test_callers.py -k target_action_summary -v`
Expected: FAIL with missing `dynamic_binding_hints`

- [ ] **Step 3: Add a target-action summary helper in `callers.py`**

```python
def _build_target_action_hints(g: GraphLookup, callback_usr: str) -> list[dict]:
    bindings = g.target_action_bindings_for_callback(callback_usr)
    if not bindings:
        return []
    return [{
        "kind": "target_action",
        "binding_count": len(bindings),
        "bindings": [
            {
                "name": item["name"],
                "file_path": item["file_path"],
                "line": item["line"],
                "control_event": item.get("control_event"),
                "callback_name": item.get("callback_name"),
            }
            for item in bindings
        ],
    }]
```

- [ ] **Step 4: Return summaries without polluting static caller data**

```python
dynamic_binding_hints = []
if not data:
    dynamic_binding_hints = _build_target_action_hints(g, req.usr)
    if dynamic_binding_hints:
        open_gaps.append("Dynamic UIKit target-action binding exists.")
```

- [ ] **Step 5: Run the caller tests to verify they pass**

Run: `pytest tests/test_mcp/test_callers.py -k target_action -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchard/handlers/callers.py tests/test_mcp/test_callers.py
git commit -m "feat: summarize dynamic target-action bindings in callers"
```

### Task 4: Add Dedicated Target-Action Graph Tool and CLI

**Files:**
- Modify: `src/orchard/server.py`
- Modify: `src/orchard/cli.py`
- Create: `src/orchard/handlers/target_action_graph.py`
- Test: `tests/test_mcp/test_target_action_graph.py`

**Interfaces:**
- Consumes: stored target-action binding records
- Produces: `orchard_target_action_graph` MCP tool and `orchard target-action-graph` CLI command

- [ ] **Step 1: Write the failing graph query tests**

```python
def test_target_action_graph_filters_by_callback_usr(conn_with_calls):
    resp = target_action_graph(conn_with_calls, TargetActionGraphRequest(
        callback_usr="c:objc(cs)ZMMyNotesToggleCell(im)onToggle:"
    ))
    assert len(resp.data["callbacks"]) == 1


def test_target_action_graph_groups_bindings_by_callback(conn_with_calls):
    resp = target_action_graph(conn_with_calls, TargetActionGraphRequest())
    callback_group = next(iter(resp.data["callbacks"].values()))
    assert callback_group["bindings"][0]["selector"] == "onToggle:"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp/test_target_action_graph.py -v`
Expected: FAIL with missing handler or command

- [ ] **Step 3: Implement the graph handler with callback grouping**

```python
@dataclass
class TargetActionGraphRequest(BaseToolRequest):
    selector: str = ""
    callback_usr: str = ""
    file: str = ""
    group_by: str = "callback"
```

```python
def target_action_graph(conn, req: TargetActionGraphRequest) -> BaseToolResponse:
    g = GraphLookup(conn)
    data = g.target_action_graph(
        selector=req.selector,
        callback_usr=req.callback_usr,
        file=req.file,
        group_by=req.group_by,
    )
    return BaseToolResponse(data=data, evidence_sources=["target_action_derivation"])
```

- [ ] **Step 4: Wire the MCP tool and CLI command**

```python
Tool(
    name="orchard_target_action_graph",
    description="Query the UIKit target-action graph grouped by callback or registrar.",
    inputSchema={...},
)
```

```python
COMMANDS["target-action-graph"] = (
    cmd_target_action_graph,
    "Query UIKit target-action bindings",
)
```

- [ ] **Step 5: Run graph and CLI-adjacent tests**

Run: `pytest tests/test_mcp/test_target_action_graph.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchard/server.py src/orchard/cli.py src/orchard/handlers/target_action_graph.py tests/test_mcp/test_target_action_graph.py
git commit -m "feat: add target-action graph query surface"
```

### Task 5: End-to-End Regression Verification and Docs Sync

**Files:**
- Modify: `skills/orchard/SKILL.md`
- Modify: `skills/orchard-debugging/SKILL.md`
- Modify: `src/orchard/setup.py`
- Test: `tests/test_mcp/test_callers.py`
- Test: `tests/test_mcp/test_references.py`
- Test: `tests/test_mcp/test_target_action_graph.py`
- Test: `tests/test_derive/test_notification_graph.py`

**Interfaces:**
- Consumes: all completed target-action query surfaces
- Produces: updated operator-facing docs and passing regression suite

- [ ] **Step 1: Add the new target-action workflow to user-facing docs**

```md
- Use `orchard_target_action_graph` when `find_callers` reports dynamic UIKit target-action bindings.
- `find_callees` and `find_references` now expose `target_action_bridges` on `addTarget:action:forControlEvents:` callees.
```

- [ ] **Step 2: Add one end-to-end regression command covering all touched areas**

Run: `pytest tests/test_derive/test_notification_graph.py tests/test_mcp/test_callers.py tests/test_mcp/test_references.py tests/test_mcp/test_target_action_graph.py -v`
Expected: PASS

- [ ] **Step 3: Run a CLI smoke test against the local Orchard repo fixture or test DB**

Run: `orchard target-action-graph --db ./.orchard/graph.db --format json`
Expected: valid JSON with either `callbacks` or an empty result envelope

- [ ] **Step 4: Review diff for scope control**

Run: `git diff --stat HEAD~4..HEAD`
Expected: changes limited to derivation, query, handler, CLI/server, tests, and skill/setup docs

- [ ] **Step 5: Commit**

```bash
git add skills/orchard/SKILL.md skills/orchard-debugging/SKILL.md src/orchard/setup.py
git commit -m "docs: document target-action query workflow"
```

## Self-Review

### Spec Coverage

- Stable-field derivation: covered by Task 1
- bridge exposure in callee/reference surfaces: covered by Task 2
- caller-side summary-only dynamic binding hints: covered by Task 3
- dedicated detailed query surface: covered by Task 4
- user-facing workflow and regression coverage: covered by Task 5

No uncovered spec requirements remain.

### Placeholder Scan

- No `TODO`, `TBD`, or deferred implementation placeholders remain.
- Every task includes concrete files, tests, commands, and expected outcomes.

### Type and Naming Consistency

- Detailed callee/reference payload uses `target_action_bridges`
- Caller summary payload uses `dynamic_binding_hints`
- Detailed graph surface is `orchard_target_action_graph`
- Stable first-version fields remain `line`, `selector`, `control_event`, `callback`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-orchard-target-action.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
