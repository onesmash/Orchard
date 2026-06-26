# Dev Perspective Review: orchard 查询层优化设计

**Date**: 2026-06-26
**Perspective**: development
**Reviewer**: Development review (code-structure and implementation-accuracy audit)
**Spec**: `docs/superpowers/specs/2026-06-26-orchard-query-optimization-from-root-cause-analysis.md`

---

## 1. Architectural Consistency with Existing Code Patterns

### PASS -- GraphLookup.methods_of() location and shape

The proposed `methods_of()` method in `src/orchard/query/lookup.py` follows the existing pattern exactly. The existing `owner_of()` already walks `Contains` edges (reverse direction: `(s)<-[:Contains]-(owner)`), and `methods_of()` would walk them forward: `(parent)-[:Contains]->(child)`. Both use `make_symbol_id()` for ID construction, matching `callers_of()` and `callees_of()`. The return type (`list[dict]`) is consistent with the existing `callers_of()` / `callees_of()` return signatures.

### PASS -- Handler-level auto-expand logic

The spec's proposed handler modification in `handlers/callers.py` calls `g.methods_of()` then iterates with `g.callers_of()` -- both are `GraphLookup` methods already used by the handler. The aggregation (dedup by USR, `via_method` annotation) is appropriate for the handler layer, matching the pattern in `handlers/impact.py` where BFS traversal, subtype closure, and depth grouping are all done within the handler.

### PASS -- Noise filter as a new query module

Adding `src/orchard/query/noise_filter.py` follows the existing layered architecture: `query/lookup.py` for graph traversal, `query/noise_filter.py` for post-processing. The handler applies the filter before returning, keeping the filter decoupled from both the query layer and the CLI/server layer.

### PASS -- audit command pattern

`cmd_audit()` in `cli.py` would follow the exact pattern of `cmd_stats()`: connect to DB, query `GraphLookup` + direct Cypher, print results. The `COMMANDS` dict registration pattern is well-established.

---

## 2. Gaps Between Design Spec and Actual Code Structure

### ISSUE -- Symbol kind filter uses wrong enumerated values

**Location**: Spec lines 30-37, `methods_of()` Cypher query

The spec proposes:
```python
WHERE child.kind IN ['method', 'instanceMethod', 'classMethod']
```

The actual ingest pipeline (`_map_indexstore_kind` in `src/orchard/pipeline/runner.py:39-44`) normalizes **all** method kinds (`instancemethod`, `staticmethod`, `classmethod`) to the single string `"method"`:
```python
if k in ("instancemethod", "staticmethod", "classmethod"):
    return "method"
```

The values `'instanceMethod'` and `'classMethod'` will **never match** any stored symbol. The filter functionally works because `'method'` is included, but the extra two values are dead code. The correct filter is simply:
```python
WHERE child.kind = 'method'
```

Or, to also catch free functions nested in types (Swift nested functions):
```python
WHERE child.kind IN ('method', 'function')
```

**Severity**: Low (works correctly by accident because `'method'` covers all methods), but misleading and sets a wrong precedent for future contributors.

### GAP -- Spec omits MCP server tool schema updates

**Location**: Multiple proposals reference `server.py` changes

The spec's risk table lists `server.py` as a changed file for proposals 2 (search --class) and 3 (noise filter), but the implementation details are missing:

1. **`--include-noise` flag**: Requires adding a new field to `CalleeRequest` dataclass and threading it through `server.py`'s `_do_handler()`, which currently hardcodes `usr=args.get("usr", "")` and `target_id=args.get("target_id", "")`. The `TOOLS` list in `server.py` is a static schema definition -- it needs a new `include_noise` boolean property.

2. **`--class` flag in search**: The `_do_search()` function in `server.py` has its own inline Cypher (separate from `cmd_search` in `cli.py`). Both paths need updating. The `TOOLS` definition for `orchard_search` needs a new `class_name` or `class` property.

3. **Class auto-expand for MCP**: The `orchard_find_callers` MCP tool handler goes through `_do_handler("callers", ...)` which constructs a `CallerRequest`. The auto-expand logic lives in the handler (`find_callers()`), so it works automatically for MCP. No server.py change needed for this proposal. **However**, the handler currently only receives `usr` and `target_id` -- if the class auto-expand needs any additional parameter (e.g., `--no-expand` to opt out), that would need threading.

**Severity**: Medium (implementation blockers if not addressed during coding).

### GAP -- Response shape inconsistency for `search --class`

**Location**: Spec lines 105-115 (output format)

The current `cmd_search()` returns `{"count": N, "results": [...]}`. The spec proposes `search --class` returns `{"owner": {...}, "methods": [...]}` -- a completely different envelope. This is a legitimate design choice, but the two response shapes from the same `orchard search` command are inconsistent. Consider:
- Option A: Return `{"count": N, "results": methods, "owner": {...}}` to preserve the outer envelope
- Option B: Document the two shapes explicitly in `--help`

**Severity**: Low (UX polish, not a blocker).

---

## 3. Technical Soundness of Auto-Expand Strategy

### PASS -- Schema supports the approach

- `Contains` edges exist as a first-class relation table (`CREATE REL TABLE IF NOT EXISTS Contains(FROM Symbol TO Symbol, ...)` in `src/orchard/graph/schema.py:113`)
- `Contains` edges are populated from IndexStore `childOf` / `containedBy` roles (`upsert_indexstore_rels()` in `src/orchard/normalize/identity.py:132-133`)
- Edge direction is **parent --[:Contains]--> child**, confirmed by:
  - `owner_of()` in `lookup.py:55`: `(s)<-[:Contains]-(owner)` -- walks **incoming** Contains to find parent
  - `upsert_indexstore_rels()` in `identity.py:279-280`: writes `(t_id, s_id)` where t_id is occurrence (container) and s_id is related (contained), matching `Contains(owner, child)`
- The spec's forward walk `(parent)-[:Contains]->(child)` is correct
- Each method symbol has its own USR, and `Calls` edges from callers point to individual method USRs (not class USRs), so per-method `callers_of()` queries will work

### PASS -- Deduplication strategy is correct

The spec's `seen` set deduplication by caller USR is necessary because a single caller may call multiple methods of the same class (e.g., `callerA` calls both `init` and `configure` on `MyClass`). Without dedup, `callerA` would appear N times. The `via_method` annotation preserves the information about which methods are called without duplicating the caller.

### SUGGESTION -- Consider annotating ALL methods called per caller

When `callerA` calls 3 methods of `MyClass`, the spec deduplicates to one entry with `via_method` set to the first-seen method name. This loses the information that `callerA` calls multiple methods. Consider:
```python
# Instead of single via_method string, use a list:
{"caller": "callerA", "via_methods": ["init", "configure", "start"]}
```
Or simpler: accumulate method names:
```python
# In the seen-check block:
if c["usr"] not in seen:
    seen[c["usr"]] = c
    c["via_methods"] = [m["name"]]
else:
    seen[c["usr"]]["via_methods"].append(m["name"])
```

**Severity**: Low (UX enhancement, not blocking).

---

## 4. Backward Compatibility of CLI Signature Changes

### PASS -- No CLI argument removals or renames

No existing flag is removed or renamed. The `--usr`, `--target`, `--db` flags remain unchanged across all commands.

### ISSUE -- `find_callers` and `find_callees` have behavioral breaking changes

The spec states: "CLI 签名: 不变。用户行为透明升级。" (CLI signature unchanged, transparent user behavior upgrade).

**However**, the behavior change IS a breaking change for any script or tool that parses `find_callers --usr c:objc(cs)MyClass` output and expects 0 results (the current behavior). After the change, the same invocation returns aggregated method callers. The output structure changes: new `via_method` field appears in each caller dict.

This is likely a **desirable** behavior change (0 results for a class USR was arguably a bug), but the spec should acknowledge it is a behavioral breaking change, not a "transparent upgrade."

### ISSUE -- Noise filtering is a DEFAULT-ON behavior change for `find_callees`

The current `find_callees` returns ALL callees. After the change, noise is filtered OUT by default. A script that relied on `find_callees` returning `LogMessage` or `operator<<` in its output would break. The `--include-noise` escape hatch is good, but the default changing is the definition of a breaking behavior change.

**Recommendation**: Either:
- Make `--include-noise` the default initially and deprecate it later, or
- Add a prominent notice in the command output when noise was filtered (as the spec already proposes with `noise_removed: N` in metadata)

### PASS -- `search --class` is purely additive

New flag, existing `search --name` behavior is unchanged. Fully backward-compatible.

### PASS -- `orchard audit` is a new command

No impact on existing commands.

---

## 5. Performance Concerns

### SUGGESTION -- Naive N+1 query pattern for large classes

**Location**: Spec lines 42-66, handler auto-expand logic

The spec proposes: for each method found, call `g.callers_of(m["usr"], target_id)`. Each `callers_of()` call executes:
1. `make_symbol_id()` (trivial)
2. One Cypher MATCH with OPTIONAL MATCH for occurrence data
3. `owner_of()` for **each** caller (which itself does a Cypher MATCH + optional extension resolution)

For a class with 100 methods, this means:
- 1 query for `methods_of()`
- 100 queries for `callers_of()`
- ~N queries for `owner_of()` (where N = total unique callers)

Total: **101+ Cypher queries per find_callers** for large classes. Some of these queries involve OPTIONAL MATCH and file occurrence joins, which are not free.

**Mitigation options** (not in spec, for consideration):

1. **Batch caller query**: After collecting all method USRs, use a single UNWIND query:
   ```cypher
   UNWIND $method_ids AS mid
   MATCH (caller:Symbol)-[:Calls]->(method:Symbol {id: mid})
   RETURN DISTINCT caller.usr, caller.name, ..., method.usr, method.name
   ```
   This replaces 100 queries with 1.

2. **Lazy owner resolution**: Defer `owner_of()` calls to only when the caller is first encountered, or batch them.

3. **Pagination**: Add a `--limit-methods N` flag for large classes to cap the number of methods expanded.

4. **Alternative via container_usr**: The Symbol node table has a `container_usr` column. A single query using this field might be faster than traversing Contains edges:
   ```cypher
   MATCH (caller:Symbol)-[:Calls]->(method:Symbol)
   WHERE method.container_usr = $class_usr
   RETURN ...
   ```
   However, `container_usr` population depends on the ingest pipeline and may not be reliable for all symbols.

**Real-world impact**: Most Objective-C classes have <50 methods. UIKit/AppKit classes with 200+ methods (e.g., UIView) are typically system symbols not in the user's target. The performance concern is theoretical for most use cases but should be validated with the biggest class in the target project.

**Severity**: Low-Medium (unlikely to cause problems in practice, but the design should note the N+1 query pattern and have a backstop).

### SUGGESTION -- Noise filter uses O(N*M) linear scan per invocation

**Location**: Spec `is_noise()` function, lines 143-147

The noise filter does `for pattern in NOISE_PATTERNS: if pattern in name` for every callee. For 14 patterns and 200 callees, this is 2,800 substring checks -- trivially fast. Not a real performance concern, but worth noting the design pattern is fine.

---

## Summary

| # | Finding | Tag | Severity |
|---|---------|-----|----------|
| 1 | `methods_of()` kind filter uses wrong values (dead `instanceMethod`/`classMethod`) | ISSUE | Low |
| 2 | MCP server TOOLS schema and `_do_handler()` changes not detailed | GAP | Medium |
| 3 | `search --class` response shape inconsistent with `search --name` | GAP | Low |
| 4 | `find_callers`/`find_callees` have behavioral breaking changes, not "transparent upgrade" | ISSUE | Medium |
| 5 | N+1 query pattern for large classes (100 methods -> 101+ queries) | SUGGESTION | Low-Medium |
| 6 | Auto-expand strategy (Contains edges + per-method callers_of) is schema-correct | PASS | -- |
| 7 | Architecture (GraphLookup + handlers + CLI) is consistent with existing patterns | PASS | -- |
| 8 | Dedup by caller USR with via_method annotation is correct | PASS | -- |
| 9 | `--include-noise` default-off is a breaking output change | ISSUE | Medium |
| 10 | `search --class` and `audit` are purely additive, no compatibility concern | PASS | -- |

## Verdict

The design is architecturally sound and the proposed file locations are correct. The key implementation blockers are:
1. Fix the kind filter values in `methods_of()` (use `'method'` or `'method', 'function'`, not `'instanceMethod'`, `'classMethod'`)
2. Detail the MCP server changes (`server.py` TOOLS schema, `_do_handler()` parameter threading)
3. Acknowledge and document the behavioral breaking changes for `find_callers`, `find_callees`, and noise filtering
4. Consider a batched UNWIND query as an optimization for large classes
