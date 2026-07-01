# Orchard Target-Action Design

Date: 2026-07-01
Status: Draft approved in chat
Scope: Orchard target-action query and UX enhancement

## Summary

This design extends Orchard's existing Objective-C semantic wiring support so UIKit `target-action` behaves more like today's notification support, without misrepresenting dynamic runtime wiring as compiler-verified static callers.

The design keeps three layers separate:

1. Static call graph results remain in existing `data` fields.
2. Dynamic UIKit binding evidence is exposed as structured bridge data.
3. Caller-oriented queries receive a lightweight dynamic binding summary when static callers are absent.

The primary user outcome is that queries like `find_callers(onToggle:)` explain why no static callers exist and still surface the most important binding evidence with low context overhead.

## Goals

- Preserve the semantic integrity of static caller results.
- Expose stable, source-backed target-action evidence using the same design philosophy as notification wiring.
- Optimize for agent context efficiency by keeping summary surfaces small and detailed surfaces explicit.
- Reuse as much of the existing notification extraction and callback-resolution pipeline as possible.

## Non-Goals

- Do not infer control type, control variable name, target object identity, or normalized event semantics beyond directly extractable source text.
- Do not synthesize fake `Calls` edges from UIKit runtime dispatch to action methods.
- Do not attempt full Objective-C AST reconstruction in the first version.

## Current State

Today Orchard already provides part of the foundation:

- `addTarget:action:forControlEvents:` is classified as `semantic_role = target_action`.
- target-action rows are already collected in `src/orchard/derive/notification_graph.py`.
- callback resolution already exists through `file_path + selector` lookup.
- CLI output already reserves a `Target-Action` section.

Current gaps:

- `find_callers(actionMethod)` returns no static callers and provides no binding summary.
- `find_callees` and `find_references` do not expose structured target-action bridge payloads analogous to `notification_bridges`.
- There is no dedicated target-action graph query surface comparable to `notification_graph`.

## Source-of-Truth Rule

Field design must follow stable, directly retrievable data only.

First-version target-action fields must come from one of:

- caller symbol graph fields: `usr`, `name`, `file_path`, `module`
- registration-site grep result: `line`
- source text extraction: `selector`, `control_event`
- callback reverse lookup: `callback.usr`, `callback.name`, `callback.module`

First version must not promise:

- `control.name`
- `target.name`
- `control.class_hint`
- normalized event categories
- inferred object graph details

## Data Model

### Persisted Target-Action Entry

Each target-action binding should use the same minimal style as notification observer entries:

```json
{
  "usr": "c:objc(cs)ZMMyNotesToggleCell(im)initWithStyle:reuseIdentifier:",
  "name": "initWithStyle:reuseIdentifier:",
  "file_path": ".../ZMMyNotesSettingsView.mm",
  "module": "Zoom",
  "line": 158,
  "selector": "onToggle:",
  "control_event": "UIControlEventValueChanged",
  "callback": {
    "usr": "c:objc(cs)ZMMyNotesToggleCell(im)onToggle:",
    "name": "onToggle:",
    "module": "Zoom"
  }
}
```

Rules:

- `control_event` is the raw token extracted from `forControlEvents:`.
- `callback` is nullable when reverse lookup fails.
- no additional inferred fields appear in v1.

### Query-Time Bridge Payload

When `find_callees` or `find_references` encounters `semantic_role = target_action`, attach:

```json
"target_action_bridges": [
  {
    "line": 158,
    "selector": "onToggle:",
    "control_event": "UIControlEventValueChanged",
    "callback": {
      "usr": "c:objc(cs)ZMMyNotesToggleCell(im)onToggle:",
      "name": "onToggle:",
      "module": "Zoom"
    }
  }
]
```

This mirrors `notification_bridges`:

- small
- structured
- source-backed
- directly useful to an agent

### Caller-Side Summary Payload

`find_callers` should not place dynamic bindings into `data`. Instead, when static callers are empty and a target-action binding exists, return:

```json
"dynamic_binding_hints": [
  {
    "kind": "target_action",
    "binding_count": 1,
    "bindings": [
      {
        "name": "initWithStyle:reuseIdentifier:",
        "file_path": ".../ZMMyNotesSettingsView.mm",
        "line": 158,
        "control_event": "UIControlEventValueChanged",
        "callback_name": "onToggle:"
      }
    ]
  }
]
```

This is intentionally a summary surface:

- no full callback object
- no duplicate module and USR data unless the user queries the detailed graph
- enough information for the agent to answer "who bound this and where"

## API Contract

### `orchard_find_callers`

Behavior:

- `data` continues to mean static callers only.
- `open_gaps` remains explanation-only.
- `dynamic_binding_hints` appears only when:
  - static caller results are empty
  - target-action binding evidence exists for `req.usr`

Recommended `open_gaps` content:

- `No static callers found.`
- `Dynamic UIKit target-action binding exists.`

### `orchard_find_callees`

Behavior:

- keep existing `semantic_role = target_action`
- add `target_action_bridges` on the `addTarget:action:forControlEvents:` callee entry

### `orchard_find_references`

Behavior:

- outgoing target-action callee entries gain `target_action_bridges`
- incoming remains static reference/caller evidence only
- no dynamic binding evidence is mixed into incoming static results

### New Tool: `orchard_target_action_graph`

Purpose:

- explicit detailed query surface for target-action wiring

Suggested inputs:

- `selector` optional
- `callback_usr` optional
- `file` optional
- `group_by` optional: `callback` or `registrar`

Default grouping: `callback`

Example output:

```json
{
  "callbacks": {
    "c:objc(cs)ZMMyNotesToggleCell(im)onToggle:": {
      "callback": {
        "usr": "c:objc(cs)ZMMyNotesToggleCell(im)onToggle:",
        "name": "onToggle:",
        "module": "Zoom"
      },
      "bindings": [
        {
          "usr": "c:objc(cs)ZMMyNotesToggleCell(im)initWithStyle:reuseIdentifier:",
          "name": "initWithStyle:reuseIdentifier:",
          "file_path": ".../ZMMyNotesSettingsView.mm",
          "module": "Zoom",
          "line": 158,
          "selector": "onToggle:",
          "control_event": "UIControlEventValueChanged"
        }
      ]
    }
  }
}
```

## CLI Contract

Add a new CLI command:

```bash
orchard target-action-graph
```

Suggested flags:

- `--selector`
- `--callback-usr`
- `--file`
- `--group-by callback|registrar`
- `--format table|json`

Default table output should be minimal:

```text
Callback: onToggle:
  ZMMyNotesToggleCell initWithStyle:reuseIdentifier:
    line 158  event UIControlEventValueChanged
```

## Implementation Plan

### 1. Derivation

File:

- `src/orchard/derive/notification_graph.py`

Changes:

- add a source-text extractor for `forControlEvents:`
- enrich existing `pending_target_actions` entries with:
  - `control_event`
  - existing `selector`
  - existing `callback`

Important:

- only extract the raw event token
- no normalization beyond preserving the exact source token

### 2. Query Lookup

File:

- `src/orchard/query/lookup.py`

Changes:

- add target-action bridge lookup similar to notification bridge enrichment
- attach `target_action_bridges` when `semantic_role == target_action`
- keep the bridge payload minimal

### 3. Handlers

Files:

- `src/orchard/handlers/callers.py`
- `src/orchard/handlers/callees.py`
- `src/orchard/handlers/references.py`

Changes:

- `callers.py`
  - when no static callers exist, query target-action binding records by callback USR
  - return `dynamic_binding_hints` summary
- `callees.py`
  - pass through `target_action_bridges`
- `references.py`
  - pass through outgoing `target_action_bridges`

### 4. Tool and CLI Surfaces

Files:

- `src/orchard/server.py`
- `src/orchard/cli.py`

Changes:

- add `orchard_target_action_graph`
- add `orchard target-action-graph`
- update tool descriptions to explain the dynamic/static distinction

## Agent Context Efficiency Rationale

This design deliberately splits summary from detail:

- `find_callers`: summary only
- `find_callees` / `find_references`: local bridge detail
- `target_action_graph`: full detailed view

This keeps the highest-frequency query cheap while still allowing detailed follow-up.

The design avoids:

- large repeated callback payloads in caller summaries
- natural-language parsing of evidence from `open_gaps`
- duplication of static and dynamic evidence in the same field

## Risks

- `forControlEvents:` extraction may miss uncommon formatting variants.
- callback resolution may fail when the selector implementation is not discoverable in the same file or indexed symbol set.
- some target-action sites may register through wrappers or macros that obscure raw event text.

These failures should degrade safely:

- `control_event = null` when not extractable
- `callback = null` when not resolvable
- no synthetic static callers added under any circumstances

## Test Plan

### Derivation Tests

File:

- `tests/test_derive/test_notification_graph.py`

Add coverage for:

- extracting selector from target-action registration
- extracting raw `control_event`
- resolving callback symbol when present
- preserving null callback when unresolved

### MCP / Handler Tests

Files:

- `tests/test_mcp/test_callers.py`
- `tests/test_mcp/test_references.py`
- `tests/test_mcp/test_target_action_graph.py`

Add coverage for:

- `find_callees` returns `semantic_role = target_action`
- `find_callees` attaches `target_action_bridges`
- `find_references` includes outgoing target-action bridge data
- `find_callers(actionMethod)` returns no static callers but includes `dynamic_binding_hints`
- `orchard_target_action_graph` filters by `callback_usr`

## Acceptance Criteria

- Querying a registrar method that binds a UIKit action returns structured `target_action_bridges`.
- Querying an action callback method with no static callers returns `dynamic_binding_hints` when a target-action binding exists.
- No target-action evidence is inserted into static caller `data`.
- `open_gaps` remains explanation-oriented rather than evidence-oriented.
- The design works for the `onToggle:` pattern demonstrated in the Zoom iOS client.

## Degrade Note

Spec review subagent loop was not executed in this run because no spawn-capable subagent tool was available in the current toolset. This document was self-reviewed against the agreed chat design and current Orchard implementation boundaries.
