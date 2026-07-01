---
name: orchard-exploring
description: "Use when the user asks how code works, wants to understand architecture, trace caller/callee relationships, inspect Objective-C / Swift graph structure, follow notification, delegate, or UIKit target-action wiring, resolve a single stack frame into code context, or explore unfamiliar Xcode-indexed code with Orchard before grepping. Examples: \"How does X work?\", \"Who calls this method?\", \"Show me the login flow\", \"I only have this stack frame\", \"Where is this notification observed?\""
---

# Exploring Codebases with Orchard

Use Orchard first when the user wants to understand an indexed Apple codebase.
Start from compiler-indexed graph facts, then read source files to explain the
implementation. This reduces guesswork and makes exploration more reliable than
jumping straight to grep.

## When to Use

- "How does this module work?"
- "Who calls this method?"
- "What does this symbol depend on?"
- "Show me the notification flow"
- "Where is this delegate callback wired up?"
- "I only have this stack frame"
- Understanding unfamiliar Objective-C / Swift code

## Workflow

```text
1. orchard_search / `orchard search --name "<concept or symbol>"`
   → resolve likely symbols, inspect status / diag / next

2. orchard_symbol / `orchard symbol --usr "<USR>"`
   → confirm identity, file path, kind, language, module

3. orchard_find_callers / orchard_find_callees
   → build the local graph neighborhood around the symbol

4. orchard_find_references or orchard_hierarchy when needed
   → combine incoming/outgoing edges or inspect type relationships

5. Read the source files that Orchard surfaced
   → explain real implementation details, not just graph structure
```

> If Orchard reports stale or unknown freshness, refresh the graph with
> `orchard ingest --project-dir .` before drawing strong conclusions.

## Checklist

```text
- [ ] Resolve the symbol or concept with orchard_search
- [ ] Read status / diag / next instead of stopping at "0 results"
- [ ] Confirm the chosen USR with orchard_symbol
- [ ] Inspect callers, callees, references, or hierarchy as needed
- [ ] Read the source files Orchard surfaced
- [ ] Summarize both graph structure and implementation behavior
```

## Tool Selection

### Guided lookup

Use `orchard_search` first when the user has:

- a symbol name
- a partial method/class name
- a concept that may map to one or more symbols
- a frame-like symbol string that may need guided resolution

Read these fields carefully:

- `matches`: direct hits
- `status.freshness`: whether the graph snapshot is trustworthy
- `status.coverage`: whether the graph likely covers this scope
- `diag`: compact reason codes such as `frame_lookup_recommended`
- `next`: Orchard's recommended next step

When the search misses, do not say "Orchard found nothing" until you inspect
`status`, `diag`, and `next`.

### Single-frame exploration

Use `orchard_lookup_frame` only when the user has one single frame or one
frame-like symbol string, for example:

```json
{"frame": "ssb::thread_wrapper_t::process_msg(unsigned int)"}
```

This is useful for turning one stack line into owner/method graph context.
Full crashlogs or full crashed-thread blocks are handled outside Orchard. First
extract one concrete frame, symbol name, qualified name, or USR, then continue
with Orchard.

### Local relationship exploration

Use these tools after you have a concrete USR:

- `orchard_find_callers`: who calls this symbol
- `orchard_find_callees`: what this symbol calls
- `orchard_find_references`: incoming + outgoing relationships in one call
- `orchard_hierarchy`: parents, protocols, subclasses

Pay attention to:

- `confidence`: compiler-verified vs inferred edges
- `call_style`: synchronous call vs async/callback boundary
- `execution_boundary`: notification callback, worker dispatch, lifecycle path
- `semantic_role`: ObjC meaning such as `notification_observer` or `delegate_setter`
- `notification_bridges`: who registered -> selector -> event key -> callback
- `target_action_bridges`: who bound target -> action -> control/event -> callback
- `dynamic_binding_hints`: runtime binding evidence when no static caller exists
- `source_scope`: whether the source file is outside the current workspace root

These fields often explain behavior better than the raw caller/callee list.

## Exploration Patterns

### "How does this symbol work?"

1. `orchard_search` by symbol name
2. `orchard_symbol` to confirm the exact USR
3. `orchard_find_callers` to see entry points
4. `orchard_find_callees` to see dependencies
5. Read the symbol's file and the most important adjacent files

### "Show me the flow"

When the user asks for a flow such as login, startup, or notification handling:

1. search for the best anchor symbol or notification name
2. inspect callers and callees
3. follow framework boundaries, delegate setters, observers, and callbacks
4. read the key implementation files in sequence
5. present the flow as ordered steps, not as an unstructured symbol dump

### "Who handles this notification or delegate?"

Prefer `orchard_find_callees` and `orchard_notification_graph` when relevant.
Use `semantic_role` and `notification_bridges` to show the full wiring:

`registrar -> selector -> notification/event -> callback`

If `orchard_find_callers` on the callback is empty, inspect
`dynamic_binding_hints` before concluding the callback is unreachable.

### "Why does this UIKit action look uncalled?"

Prefer `orchard_find_callers` first, but do not stop at an empty static caller
set. For UIKit callbacks, inspect `dynamic_binding_hints` and use
`orchard_target_action_graph` to show the full wiring:

`binder -> target/action registration -> control/event -> callback`

### "Why does this notification callback look uncalled?"

Prefer `orchard_find_callers` first, but do not stop at an empty static caller
set. For notification callbacks, inspect `dynamic_binding_hints` and use
`orchard_notification_graph` to show the full wiring:

`poster -> notification -> observer registration -> callback`

### "I only have this stack frame"

1. use `orchard_lookup_frame`
2. inspect the resolved owner/method candidates
3. use `orchard_find_callers` / `orchard_find_callees` on the chosen USR
4. read the source file
5. explain what thread/callback/lifecycle boundary the frame appears to sit on

## CLI Fallback

Prefer MCP tools when available. If only the CLI is available, use:

```bash
orchard search --name "<text>"
orchard symbol --usr "<USR>"
orchard find_callers --usr "<USR>"
orchard find_callees --usr "<USR>"
orchard find_references --usr "<USR>"
orchard hierarchy --usr "<USR>"
orchard notification-graph -n "kNoti_LogoutForUI"
orchard target-action-graph -a "onToggle:"
```

If you need 3 or more related queries, prefer pipe mode:

```bash
echo '{"cmd":"search","args":{"name":"viewDidLoad","limit":5}}
{"cmd":"symbol","args":{"usr":"<USR>"}}
{"cmd":"find_callers","args":{"usr":"<USR>"}}' | orchard pipe
```

## Output Style

When answering the user:

1. Start with the direct answer: what the symbol/module does.
2. Show the important entry points or callers.
3. Show the important dependencies or callees.
4. Mention relevant boundaries: notifications, delegates, async hops, lifecycle.
5. When static callers are absent, say whether Orchard found dynamic binding hints.
6. Cite concrete files for implementation details.
7. Call out uncertainty when Orchard freshness/coverage is limited.

Do not flood the user with every edge. Curate the graph into a readable mental
model.

## Example: "How does notification logout handling work?"

```text
1. orchard_search("LogoutForUI")
   → direct symbol/notification candidates
2. orchard_notification_graph(notification_name="kNoti_LogoutForUI")
   → posters, observers, selectors, callbacks
3. orchard_symbol("<observer callback usr>")
   → callback file path and owner
4. orchard_find_callers("<observer callback usr>")
   → see how the callback joins the rest of the flow
5. Read the surfaced source files
```

## Example: "Why can't find_callers see onToggle:?"

```text
1. orchard_search("onToggle:")
   → resolve the action selector symbol
2. orchard_find_callers("<action usr>")
   → static callers may be empty
3. inspect dynamic_binding_hints on the caller/reference-side results
4. orchard_target_action_graph(action_name="onToggle:")
   → binder, target, control/event, callback
5. Read the surfaced source files
```

## Example: "Why can't find_callers see onMyNotesPageRefreshed:?"

```text
1. orchard_search("onMyNotesPageRefreshed:")
   → resolve the notification callback symbol
2. orchard_find_callers("<callback usr>")
   → static callers may be empty
3. inspect dynamic_binding_hints on the caller results
4. orchard_notification_graph(notification_name="kNoti_MyNotes_PageRefreshed")
   → posters, observer registration, callback
5. Read the surfaced source files
```

## Important Constraints

- Use Orchard for graph-enriched code exploration, not as a full crashlog analyzer.
- Do not claim exact C++ field offsets or ABI layout from Orchard/IndexStore data.
- Treat stale or uncovered search results as incomplete evidence, not proof of absence.
- Prefer Orchard-native next steps over ad-hoc grep when search guidance is available.
