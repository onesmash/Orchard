---
name: orchard-impact-analysis
description: "Use when the user wants to know what will break if they change a symbol, whether a modification is safe, or what code depends on a method/class/protocol in an Orchard-indexed Apple codebase. Examples: \"Is it safe to change this?\", \"What depends on this method?\", \"Show me the blast radius\", \"What callers do I need to update?\" Make sure to use this skill before non-trivial edits, refactors, or renames, and whenever the user asks for safety analysis or dependency impact rather than only code understanding."
---

# Impact Analysis with Orchard

Use Orchard before edits to understand dependency blast radius from the graph,
not guess from grep. Start with `orchard_impact`, interpret its summary, then
use depth groups and adjacent graph tools to turn that result into an edit and
test plan.

## When to Use

- "Is it safe to change this function?"
- "What will break if I modify X?"
- "Show me the blast radius"
- "Who depends on this code?"
- Before refactoring, renaming, or changing shared behavior
- Before committing, when the user wants to sanity-check impact

## Workflow

```text
1. orchard_impact({usr: "<USR>"})
   → summary + by_depth + risk

2. Review d1 first
   → direct callers, subclasses, protocol conformers that will break first

3. Use orchard_symbol / orchard_find_callers / orchard_find_references as needed
   → confirm identity and investigate sensitive dependents

4. Assess risk, affected surfaces, and likely tests
   → tell the user what must be updated and what should be tested
```

> If impact freshness is `stale` or the risk is `critical`, refresh the graph
> with `orchard ingest --project-dir .` before treating the blast radius as
> complete.

## Checklist

```text
- [ ] Run orchard_impact on the exact target symbol
- [ ] Read data.summary first
- [ ] Review d1 dependents before anything else
- [ ] Check whether cross-language bridges are involved
- [ ] Use adjacent tools to inspect sensitive callers or references
- [ ] Report risk to the user before proposing edits
- [ ] Convert impact into an update/test plan
```

## Understanding Output

### Summary

`orchard_impact` includes a compact `summary` designed for the first
human-facing answer:

- `risk`
- `direct_callers`
- `primary_surface`
- `d2_clusters`
- `likely_tests`

Use this summary for the top-line answer, then use `by_depth` for detail.

### Depth groups

| Depth | Meaning |
| --- | --- |
| `d1` | WILL BREAK — direct callers, subclasses, protocol conformers |
| `d2` | LIKELY AFFECTED — callers of callers / adjacent surfaces |
| `d3+` | MAY NEED TESTING — transitive impact |

Treat `d1` as the set that needs immediate attention before code changes are
declared safe.

### Risk levels

| Level | Meaning |
| --- | --- |
| `critical` | graph freshness is not trustworthy |
| `high` | many direct dependents, or cross-language bridge pressure |
| `medium` | moderate direct dependency fan-out |
| `low` | small direct dependency set |

Warn the user explicitly before proceeding on `high` or `critical`.

## Primary Tool

### orchard_impact

Use Orchard impact on the exact USR:

```text
orchard_impact({usr: "<USR>"})
```

CLI equivalent:

```bash
orchard impact --usr "<USR>"
```

The result gives you:

- `data.summary`
- `data.by_depth`
- `data.risk`
- freshness state

This is the default starting point for safety analysis.

## Supporting Tools

### orchard_symbol

Use `orchard_symbol` first when you need to confirm you have the exact symbol
before trusting an impact run.

### orchard_find_callers

Use `orchard_find_callers` after impact when:

- a d1 caller looks especially sensitive
- you need to inspect the local calling shape
- you want to understand callback or lifecycle boundaries around a dependent

For UIKit target-action callbacks, remember that static callers may be empty
even when the method is live. In that case, inspect `dynamic_binding_hints`
and use `orchard_target_action_graph` before concluding the blast radius is
small.

### orchard_find_references

Use `orchard_find_references` when the target may participate in ObjC ↔ Swift
bridges or when you want one compact incoming/outgoing view around a dependent.

This is especially useful for verifying whether a risky symbol sits on a
cross-language or framework boundary.

### orchard_target_action_graph

Use `orchard_target_action_graph` when the target symbol is a UIKit action
selector or when impact/caller output suggests runtime binding instead of a
normal static call edge.

This is especially useful for recovering real binding records:

- which object installed the binding
- which control/event pair triggers the callback
- which target/action selector is invoked at runtime

## Orchard-Specific Risk Signals

### Freshness drives trust

If the impact result is not fresh, Orchard intentionally treats risk more
conservatively. That means:

- stale graph → do not understate blast radius
- `critical` risk may come from trustability, not only fan-out

So the first question is not just "how many dependents?" but also "can we
trust this snapshot?"

### Cross-language bridges matter

Orchard raises risk when dependents involve ObjC ↔ Swift bridge edges.

Why this matters:

- bridge-heavy symbols often support shared APIs
- breakage can surface in multiple language layers
- testing usually needs to cover both sides of the bridge

When bridge pressure is present, use `orchard_find_references` to inspect the
bridge-adjacent edges more closely.

### Dynamic bindings matter too

Some Apple-framework entry points are not modeled as ordinary static callers.
UIKit target-action is the common case: the callback may have low apparent
fan-out in `find_callers` while still being user-reachable through runtime
binding.

When this signal appears:

- do not treat "0 static callers" as proof of safety
- inspect `dynamic_binding_hints`
- use `orchard_target_action_graph` to recover the binding-side surface

### Primary surface and d2 clusters

`primary_surface` and `d2_clusters` help translate raw graph reachability into
something a human can act on:

- `primary_surface` hints at the main functional area hit first
- `d2_clusters` hints at the next layer of surfaces likely to be affected

Use these to tell the user where to focus testing, not just how many symbols
exist.

## Common Scenarios

### "What breaks if I change this method?"

1. resolve the exact USR if needed
2. run `orchard_impact`
3. review `summary`
4. enumerate d1 dependents
5. if it is a callback-style API, check for dynamic binding evidence
6. call out d2 clusters and likely tests

### "Can I rename this safely?"

1. run `orchard_impact`
2. confirm d1 callers / subclasses / conformers
3. if risk is acceptable, proceed to rename workflow
4. ensure the user understands which callers must change

### "This protocol or shared utility feels risky"

1. run impact on the protocol or utility symbol
2. inspect d1 conformers or direct callers
3. pay extra attention to bridge-related risk
4. convert likely tests into a concrete validation list

### "I changed something already, what should I test?"

1. run impact on the changed symbol
2. read `likely_tests`
3. inspect `primary_surface` and `d2_clusters`
4. for callback selectors, add binding-side entry points from target-action data
5. turn those into an ordered test recommendation

## How to Report Results

When answering the user:

1. start with the summary:
   `Risk is medium; 4 direct callers; primary surface is X; likely tests are Y`
2. list the d1 dependents that must be updated or reviewed
3. mention dynamic binding caveats when static callers undercount runtime reachability
4. mention d2/d3 as testing scope, not guaranteed breakage
5. call out stale freshness or bridge-related caveats
6. end with a concrete recommendation:
   safe to proceed, proceed carefully, or refresh/index first

Do not dump raw graph JSON without translating it into edit risk.

## Example: "Is it safe to change this shared callback?"

```text
1. orchard_impact({usr: "<USR>"})
   → summary: risk=high, direct_callers=6, primary_surface=meeting lifecycle
2. review d1
   → 6 direct dependents including lifecycle and callback entry points
3. inspect bridge-heavy dependents with orchard_find_references
4. recommendation
   → not a local-only change; update all d1 callers and test lifecycle + bridge callers
```

## Important Constraints

- Impact analysis is the first safety tool before edits, not a replacement for
  reading the code.
- A stale graph means the result is incomplete evidence; refresh first.
- `d2` and `d3+` are testing guidance, not guaranteed direct breakage.
- High-risk results should be surfaced to the user before proposing code changes.
