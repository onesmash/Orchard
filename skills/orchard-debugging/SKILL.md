---
name: orchard-debugging
description: "Use when the user is debugging a bug, tracing an error, following a crash frame, or asking why code behaves unexpectedly in an Apple codebase indexed by Orchard. Examples: \"Why is this failing?\", \"Trace this crash frame\", \"Who triggers this callback?\", \"Where does this notification end up?\" Make sure to use this skill whenever the user has a symptom, stack line, stale/missing symbol hit, async callback suspicion, notification/delegate wiring question, or wants a graph-assisted debugging path instead of ad-hoc grep."
---

# Debugging with Orchard

Use Orchard to turn a vague symptom into a concrete debugging path. Start from
the symptom the user has, resolve it to compiler-indexed symbols, then use
caller/callee/reference data plus freshness and boundary annotations to narrow
root-cause hypotheses.

## When to Use

- "Why is this function failing?"
- "Trace where this crash frame comes from"
- "Who calls this callback?"
- "Why didn't this notification handler run?"
- "This behavior looks async/racy"
- Investigating bugs, crashes, stale symbol hits, or unexpected behavior

## Workflow

```text
1. orchard_search({name: "<error text, symbol, or symptom>"})
   → find likely symbols, inspect status / diag / next

2. If the user only has one frame:
   orchard_lookup_frame({frame: "<stack line>"})
   → resolve owner/method candidates and graph context

3. orchard_symbol({usr: "<USR>"})
   → confirm identity, file path, module, language

4. orchard_find_callers / orchard_find_callees / orchard_find_references
   → trace incoming and outgoing graph relationships

5. Read source files and compare them with Orchard's boundaries / freshness
   hints to confirm the actual root cause
```

> If freshness is `stale` or `unknown`, refresh the graph with
> `orchard ingest --project-dir .` before making strong claims from a miss or a
> suspicious call graph gap.

## Checklist

```text
- [ ] Understand the symptom: error, crash frame, wrong behavior, missing callback
- [ ] Use orchard_search or orchard_lookup_frame as the entry point
- [ ] Read status / freshness / coverage / diag / next before trusting a miss
- [ ] Confirm the suspect symbol with orchard_symbol
- [ ] Trace callers, callees, or references around the suspect
- [ ] Pay attention to async boundaries, notification wiring, and source_scope
- [ ] Read the actual source files to confirm root cause
```

## Debugging Patterns

| Symptom | Orchard Approach |
| --- | --- |
| Error text or suspect symbol | `orchard_search` → `orchard_symbol` → callers/callees |
| One crash frame | `orchard_lookup_frame` → references/callers on chosen method |
| Missing callback | `orchard_find_callers` + `execution_boundary` |
| Wrong notification behavior | `orchard_find_callees` / `orchard_notification_graph` |
| Delegate or target-action confusion | `semantic_role`, `target_action_bridges`, `orchard_target_action_graph` |
| Search miss or stale results | inspect `freshness`, `coverage`, `diag`, `next` before grep |
| Suspected regression before editing | `orchard_impact` to see who depends on the suspect |

## Entry Points

### 1. Start from symptom text or symbol fragments

Use `orchard_search` when the user has:

- an error string
- a function/class/method name
- a partial symbol fragment
- a concept like "logout notification" or "delegate callback"

Look at:

- `matches`
- `status.freshness`
- `status.coverage`
- `diag`
- `next`

These tell you whether the miss is real, stale, uncovered, frame-like, or
needs a more targeted next step.

### 2. Start from one stack frame

Use `orchard_lookup_frame` when the user has one single stack frame or
frame-like symbol string.

This is especially useful for:

- one crash frame
- one thread entry that looks unfamiliar
- C++ / ObjC / Swift frame text with owner and method structure

After lookup:

1. pick the most plausible owner/method candidate
2. inspect `source_scope`
3. inspect `execution_boundary`
4. follow up with `orchard_find_references`, `orchard_find_callers`, or
   `orchard_find_callees`

Full crashlogs are handled outside Orchard. Extract one concrete frame first.

## Relationship Tracing

### callers — "Who can trigger this?"

Use `orchard_find_callers` to identify entry points and upstream triggers.

Pay attention to:

- `confidence`
- `call_style`
- `execution_boundary`
- `source_scope`

`execution_boundary` is especially useful when the suspect symbol is reached
through:

- SDK callbacks
- worker-thread dispatch
- main-thread tasks
- notification callback sinks
- lifecycle/uninit paths

### callees — "What does this depend on?"

Use `orchard_find_callees` when the bug may come from downstream dependencies.

Pay attention to:

- `semantic_role`
- `notification_bridges`
- `call_style`
- `execution_boundary`

This helps answer questions like:

- does this method register for notifications?
- is it setting a delegate or data source?
- is this actually an Apple framework callback?
- what callback is the notification observer wired to?
- if this is a UIKit action method, where was it bound and with which control event?

When `find_callers` returns no static callers for a callback-style method,
check `dynamic_binding_hints` first. For notification callbacks, follow up with
`orchard_notification_graph`; for UIKit action methods, use
`orchard_target_action_graph` for full binding details.

### references — "Show me both sides"

Use `orchard_find_references` when the user needs one compact view of
incoming + outgoing relationships around a suspect symbol.

This is often the fastest way to orient yourself before opening source files.

## How to Interpret Orchard-Specific Signals

### freshness vs coverage

- `freshness` says whether the indexed snapshot is trustworthy
- `coverage` says whether the graph likely covers the searched scope

Do not treat these as the same thing.

Examples:

- `fresh` + `unknown coverage`: the graph is current, but this scope may not be
  covered
- `stale` + direct miss: do not over-trust the miss; refresh first

### next actions

Prefer Orchard's `next` actions over improvising:

- search owner/type
- switch to `orchard_lookup_frame`
- refresh the graph
- fall back to text search

### source_scope

If `source_scope.status` is `outside_workspace_root`, Orchard may have found
the symbol in a sibling checkout or external path even when grep under the
current workspace finds nothing.

## Common Debugging Scenarios

### "Why is this crash happening around this frame?"

1. `orchard_lookup_frame`
2. inspect candidate owner/method and `execution_boundary`
3. `orchard_find_callers` on the chosen method
4. `orchard_find_callees` for downstream dependencies
5. read the implementation files

Use Orchard to determine whether the frame sits on a worker dispatch path,
framework callback path, or lifecycle edge before making hypotheses.

### "Why didn't the notification trigger the expected code?"

1. search for the notification name or known observer callback
2. if `find_callers` on the callback is empty, inspect `dynamic_binding_hints`
3. `orchard_find_callees` on the registrar if known
4. inspect `notification_bridges`
5. use `orchard_notification_graph` when a notification-centric view is clearer
6. read the observer and callback implementations

### "This async callback seems to happen from nowhere"

1. search the callback symbol
2. inspect callers
3. if callers are empty, inspect `dynamic_binding_hints`
4. inspect `call_style` and `execution_boundary`
5. check whether the path enters from SDK callback / main-thread / worker-thread
   boundaries

### "Search says nothing, but I know this exists"

1. inspect `freshness`
2. inspect `coverage`
3. read `diag`
4. execute `next`
5. if Orchard recommends refresh, run `orchard ingest --project-dir .`
6. only then fall back to shell text search

## Use Impact Carefully During Debugging

`orchard_impact` is not the first debugging tool, but it helps when:

- the user is considering a fix and wants to know blast radius
- a suspect symbol is shared widely
- you need to distinguish a local bug from a risky shared abstraction

Warn the user if risk is `high` or `critical`.

## Output Style

When answering the user:

1. restate the symptom in concrete terms
2. identify the most likely suspect symbol(s)
3. show the graph evidence that supports that suspicion
4. explain relevant boundaries: async, notification, delegate, lifecycle
5. call out freshness/coverage limits if present
6. end with the most plausible root-cause hypothesis and the next source files
   or checks

Do not dump raw graph data without interpretation.

## Example: "I only have this stack line and the callback seems wrong"

```text
1. orchard_lookup_frame("ssb::thread_wrapper_t::process_msg(unsigned int)")
   → owner/method candidates, source_scope, execution_boundary
2. orchard_find_callers("<chosen usr>")
   → identify upstream dispatch path
3. orchard_find_callees("<chosen usr>")
   → inspect downstream dependencies and semantic roles
4. Read the surfaced source files
5. Explain whether this is a worker-thread entry, lifecycle path, or callback sink
```

## Important Constraints

- Orchard helps debug from explicit symbol identity and graph context; it is
  not a full crashlog interpreter.
- Do not claim exact C++ member offsets from Orchard / IndexStore data alone.
- Treat stale graphs and uncovered scopes as incomplete evidence.
- Use Orchard to narrow hypotheses, then confirm them in source.
