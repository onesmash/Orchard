---
name: orchard
description: >
  Query the Orchard Apple Semantic Graph to analyze code relationships in
  indexed Xcode projects. Use this skill whenever the user asks about
  function callers or callees, impact / blast-radius analysis, type
  hierarchies, symbol lookups, guided miss-path debugging, crash-frame
  lookup, crashed-thread triage, ARM64 register clues such as `x0 = 0`,
  code dependencies, "who calls X", "what does Y depend on",
  "I only have this stack frame", ObjC notification/delegate wiring,
  lifecycle / async callback boundaries, safe renaming, or wants to understand how iOS / macOS components
  relate to each other. Also use it when the user mentions "orchard"
  directly, or asks for a graph-based view of their Objective-C / Swift
  codebase. Make sure to use this skill even when the user only has a
  symbol fragment, stale search result, or crash stack line and needs
  the next debugging step, not just a raw symbol search. Every edge is
  compiler-verified via Xcode IndexStore — "confidence" labels tell you
  whether a call edge is source-level evidence or compiler-inferred.
---

# Orchard — Semantic Graph CLI

The `orchard` CLI queries a semantic code graph (Ladybug/KuzuDB) built from
Xcode IndexStore data — every edge is compiler-verified.  The database lives at
``<project>/.orchard/graph.db`` and is auto-discovered by walking up from cwd.

## Quick check

```bash
orchard --help
```

## DB discovery

Commands find the database automatically:

1. `--db <path>` flag
2. `ORCHARD_DB_PATH` environment variable
3. Walk up from cwd → first `.orchard/graph.db` found
4. `~/.orchard/graph.db` (global fallback)

**Rarely need `--db`** — just run queries from anywhere under the project.

Important distinction:

- `--db` points to Orchard's graph database file, usually
  `<project>/.orchard/graph.db`
- `--index-store` points to Xcode's IndexStore `.../Index.noindex/DataStore`

Do not pass a DerivedData directory to `--db`.  If the user gives you a path
under `~/Library/Developer/Xcode/DerivedData/...` or a custom Xcode cache
directory, that is usually an `--index-store` / DerivedData hint, not the
graph DB itself.

## Guided symbol lookup

```bash
orchard search --name "<text>" [--kind <kind>] [--language <swift|objc>] [--limit 20]
```

The `--name` flag does substring matching. Regex works too:

- Substring (default): `--name "release"` → `.*release.*`
- Exact match: `--name "^viewDidLoad$"`
- Prefix match: `--name "^MyClass.*"`

`orchard_search` is now a guided lookup surface.  Its MCP response centers on:

- `query` — how Orchard interpreted the input (`symbol`, `qualified_symbol`, `frame`, ...)
- `status` — compact `outcome`, `coverage`, and `freshness`
- `matches` — direct symbol hits
- `diag` — short diagnostic codes such as `frame_lookup_recommended`,
  `index_stale`, `text_fallback_recommended`
- `candidates` — compact fallback buckets such as `symbols`, `owners`, and `text`
- `next` — executable next actions, not prose explanations

When the query misses, do not stop at "0 results". Read `status`, `diag`, and
`next` to decide whether to:

- narrow to an owner/type search
- switch to `orchard_lookup_frame`
- refresh the index via the `orchard_refresh_index` maintenance action
- fall back to shell text search

If ambiguous, prefer following `next` or narrowing by `module`, `kind`, or
`language` before asking the user for clarification.

## Crash-frame lookup

Use `orchard_lookup_frame` when the user has a stack frame or crash fragment:

```json
{"frame": "ssb::thread_wrapper_t::process_msg(unsigned int)"}
```

Use it for:

- a single crash frame
- a frame-like symbol with namespace, owner, and parameters
- "I only have this stack line, where do I start?"

Do **not** manually translate the frame into several separate searches first.
Let Orchard parse the frame, attempt qualified lookup, fall back through owner
plus method resolution, then return direct callers and next actions.

Use `orchard_lookup_crash_thread` when the user has pasted the crashed-thread
block rather than one frame. It resolves parseable application frames, reports
the first indexed business symbol, direct callers, next actions, and likely
thread/dispatch boundaries such as `process_msg`, target-action, SDK callbacks,
or notification callbacks.

Crash-thread responses may include:

- `summary.business_first_frame` — the first indexed application/business frame
- `summary.direct_callers` — compiler-indexed direct callers of that frame
- `summary.thread_boundaries` / `dispatch_boundaries` — frames that look like
  SDK callbacks, worker dispatch, main-thread tasks, notification/callback sinks,
  or lifecycle teardown paths
- `summary.next_actions` / top-level `next` — executable Orchard follow-ups
- `summary.register_semantics` — register-derived crash clues when present

For ARM64 C++ instance-method frames, if the crash report includes `x0 = 0` or
`x0: 0x0`, treat it as strong triage evidence that `this` is null. Orchard
annotates this as `diag: arm64_null_this` and
`likely_fault: null_this_dereference`. Prefer this interpretation before
blaming a member value such as `using_scene_`.

Do not claim Orchard has exact C++ object field offsets from IndexStore. If a
crash mentions an address such as `0x20`, use it only as a hypothesis to check
against source or compiler layout data. Exact class/member offsets require
DWARF debug info, Clang record layout output, or another ABI-aware source, not
IndexStore alone.

If `orchard_search` returns `frame_lookup_recommended`, call
`orchard_lookup_frame` next instead of improvising.

## Query commands

### find_callers — Who calls this symbol?

```bash
orchard find_callers --usr "<USR>"
```

Each caller includes:
- `confidence`: `compiler-verified` (source_direct) or `inferred` (indexstore_relation_only)
- `provenance`: raw reason from the IndexStore edge
- `file_path` from Symbol table (line/col not persisted; use grep for precise locations)
- `call_style`: `synchronous_call` or `async_or_callback_boundary`
- `execution_boundary`: heuristic role when the caller looks like a boundary
  (`sdk_callback`, `worker_thread_dispatch`, `main_thread_task`,
  `notification_callback_sink`, `lifecycle_uninit_path`)
- `source_scope`: whether `file_path` is inside or outside the active workspace root

Use `call_style` and `execution_boundary` to turn "A calls B" into a better
crash hypothesis. A chain like `GetUsingScene <- GetMicUsingScene <- process_msg`
is not just a caller chain; `process_msg` suggests a worker-thread dispatch
boundary, so lifecycle/race hypotheses deserve attention.

### find_callees — What does this symbol call?

```bash
orchard find_callees --usr "<USR>"
```

Each callee includes the same `confidence` + `provenance` fields.  **ObjC callees
also carry `semantic_role`** — the selector classified into one of:

| semantic_role | Example selector |
|---------------|-----------------|
| `notification_observer` | `addObserver:selector:name:object:` |
| `notification_poster` | `postNotificationName:object:` |
| `delegate_setter` | `setDelegate:` |
| `data_source` | `setDataSource:` |
| `target_action` | `addTarget:action:forControlEvents:` |
| `framework_callback` | `viewDidLoad`, `application:didFinish…` |
| `unknown` | anything else |

This tells you at a glance whether a symbol registers for notifications, sets
up delegate wiring, or is an Apple framework entry point.

Callees also carry `call_style`, `execution_boundary`, and `source_scope` when
Orchard can infer them. Use these fields to distinguish normal synchronous
calls from SDK callbacks, dispatch hops, main-thread tasks, notification sinks,
and lifecycle/uninit paths.

**Notification bridges**: `find_callees` now includes `notification_bridges`
by default for `notification_observer` callees — showing which notification
name, @selector, and callback each observer is wired to.  This gives you
the full chain: **who registered → selector → event key → callback**.

### find_references — Incoming + outgoing in one call

```bash
orchard find_references --usr "<USR>"
```

Returns `incoming` (callers) and `outgoing` (callees) with the same
`confidence` + `provenance` as find_callers/find_callees.  **ObjC callees
also carry `semantic_role`** — notification_observer, delegate_setter,
framework_callback, etc.

### impact — Blast-radius analysis

```bash
orchard impact --usr "<USR>" [--max-depth 5]
```

Returns dependents grouped by depth and a risk level.  Includes **subtype
closure** (protocol conformers, subclasses) in d1.

Impact responses also include a compact `summary`:

- `risk`
- `direct_callers`
- `primary_surface`
- `d2_clusters`
- `likely_tests`

Prefer this summary for the first human-facing sentence, then cite the detailed
`by_depth` groups for the actual blast radius.

**Depth groups:**

| Depth | Meaning |
|-------|---------|
| `d1` | WILL BREAK — direct callers, subtypes, protocol conformers |
| `d2` | LIKELY AFFECTED — callers of callers |
| `d3+` | MAY NEED TESTING — transitive dependents |

**Risk levels:**

| Level | Condition |
|-------|-----------|
| `critical` | Graph index is stale |
| `high` | ≥10 direct dependents, or cross-language bridges with ≥4 |
| `medium` | 4–9 direct dependents |
| `low` | <4 direct dependents |

**Always warn the user** before proposing changes to HIGH or CRITICAL risk symbols.

## Guided miss-path workflow

When Orchard search does not directly resolve the symbol, use this sequence:

1. **Check `status.freshness`**
   If `stale` or `unknown`, treat the miss as suspect and consider the
   `orchard_refresh_index` maintenance action before over-trusting absence.
2. **Check `status.coverage`**
   Distinguish `covered` from `partial` / `uncovered` / `unknown`.
3. **Read `diag`**
   Use short diagnostic codes to understand the likely reason for the miss.
4. **Execute `next`**
   Prefer Orchard-native next steps over ad-hoc grep.

Important interpretation:

- `freshness` answers whether the build snapshot is trustworthy
- `coverage` answers whether the graph likely covers the searched scope

Do not conflate them. A query can be fresh but uncovered, or covered but stale.

### symbol — Metadata

```bash
orchard symbol --usr "<USR>"
```

Returns name, kind, language, module, file_path, signature, access_level.

### hierarchy — Type hierarchy

```bash
orchard hierarchy --usr "<USR>"
```

Returns parents, protocols, and children/subclasses.

### rename — USR-precise symbol rename

```bash
# Dry-run preview (default):
orchard rename --usr "<USR>" --new-name "<newName>"

# Actually apply:
orchard rename --usr "<USR>" --new-name "<newName>" --no-dry-run
```

Uses Symbol + Calls tables to find affected files and performs
word-boundary text search-and-replace.  Always dry-run first.
Changes can be reverted with `git checkout`.

### notification-graph — NSNotificationCenter wiring

CLI:

```bash
# Show all notification chains:
orchard notification-graph

# Filter by notification name:
orchard notification-graph -n kNoti_LogoutForUI

# JSON output:
orchard notification-graph -f json
```

MCP: `orchard_notification_graph({notification_name: "...", group_by: "observer"})`

Two views:
- `group_by: "notification"` (default) — grouped by notification name, each
  with posters and observers.  Observers now carry full identity (`usr`,
  `name`, `file_path`), `selector`, and `callback` — the complete
  **notification_bridge**: who registered → @selector → event key → callback.
- `group_by: "observer"` — pivoted by observer USR, showing each observer's
  registrations at a glance.

Shows poster → [notification] → callback chains.  During ingest,
Notification nodes and Posts/Observes edges are persisted to the graph.
Query them directly via Cypher for instant answers:

```cypher
-- Full chain: poster → notification → callback (with observer identity)
MATCH (p:Symbol)-[:Posts]->(n:Notification)-[o:Observes]->(cb:Symbol)
RETURN DISTINCT p.name, n.name, o.observer_name, o.selector, cb.name

-- Who observes this notification?
MATCH (n:Notification {name: 'kNoti_X'})-[o:Observes]->(cb:Symbol)
RETURN DISTINCT o.observer_name, o.selector, cb.name

-- All registrations by a specific observer
MATCH (n:Notification)-[o:Observes]->(cb:Symbol)
WHERE o.observer_usr = '<USR>'
RETURN n.name, o.selector, cb.name
```

### stats — Database overview

```bash
orchard stats
```

### audit — Module coverage

```bash
orchard audit [--project-dir <path>]
```

Shows per-module symbol counts and flags gaps (< 100 symbols for a framework
target).

## Graph Schema

| Node | Purpose |
|------|---------|
| `Symbol` | Compiler-verified symbol |
| `Notification` | Notification name extracted from source via grep |
| `File` | Source file path |

| Edge | Meaning | Source |
|------|---------|--------|
| `Calls` | A calls B | IndexStore (compiler-verified) |
| `Posts` | A posts notification N | derive/notification |
| `Observes` | N notifies callback C (carries observer_usr/name/file_path + selector) | derive/notification |
| `Contains` | Class contains method | IndexStore |
| `BridgesTo` | ObjC ↔ Swift | derive/bridge |

## Ingest — building the graph

```bash
orchard ingest --project-dir /path/to/YourProject
```

Auto-detects the Xcode workspace, DerivedData, IndexStore, source root, and target.
Writes DB to `<project>/.orchard/graph.db`.  Notification/Posts/Observes are
automatically extracted from source during ingest.  Incremental mode re-scans
only changed files.

When the user provides paths explicitly, the common real-world form is:

```bash
orchard ingest \
  --project-dir /path/to/project \
  --index-store /path/to/DerivedData/.../Index.noindex/DataStore \
  --target Zoom
```

### Ingest progress

During a real ingest, progress now appears in phases instead of staying silent:

- `ingest: reading index store...`
- streamed `orchard-indexstore-reader` progress lines from stderr
- `communities: deriving graph partitions...`
- `notification-graph: scanning source files...`
- `processes: detecting execution flows...`

If the user says ingest is "stuck" or "没有 progress", first distinguish:

1. **Fast path**: incremental ingest may exit quickly with
   `incremental: fast path hit`
2. **Reader progress**: IndexStore scanning should emit live
   `[orchard-indexstore-reader +Xs] ...` lines
3. **Long derive phase**: notification graph and community/process derivation
   can take substantial time after symbol/relation counts are printed

So the debugging question is not just "is it slow?", but **which phase is
currently running and whether progress output is still advancing**.

### Freshness after ingest

`orchard ingest` now writes a `BuildSnapshot` for the current graph build.
After a successful ingest, normal queries such as `symbol`, `impact`,
`find_callers`, and `find_callees` should usually return `freshness: "fresh"`
instead of perpetual `"stale"` (unless the graph is genuinely outdated or the
query uses a mismatched build context).

## Pipe mode — batch queries

Prefer pipe when running 3+ queries:

```bash
echo '{"cmd":"search","args":{"name":"viewDidLoad","limit":5}}
{"cmd":"find_callers","args":{"usr":"<USR>"}}
{"cmd":"impact","args":{"usr":"<USR>"}}' | orchard pipe
```

## MCP server

All orchard tools are available as MCP tools with session-scoped DB connection.
The skill should prefer MCP tools when available; fall back to CLI pipe for
batch queries.

MCP tools: `orchard_search`, `orchard_lookup_frame`, `orchard_lookup_crash_thread`, `orchard_find_callers`, `orchard_find_callees`
(returns notification_bridges by default for ObjC observers),
`orchard_find_references` (includes semantic_role for ObjC callees),
`orchard_notification_graph` (with `group_by: "observer"` for
by-observer view), `orchard_impact`, `orchard_symbol`,
`orchard_hierarchy`, `orchard_rename`, `orchard_stats`, `orchard_audit`.

## Confidence labels

Every caller/callee now carries a `confidence` field:

| confidence | Edge reason | Meaning |
|-----------|-------------|---------|
| `compiler-verified` | `source_direct` | Observed at a source-level call-site |
| `compiler-verified` | `NULL` | Edges from symbolgraph (explicitly declared) |
| `inferred` | `indexstore_relation_only` | Compiler type-inference (protocol dispatch, overrides) |

The `include_inferred` flag controls whether inferred edges appear.

## Typical workflow

1. **Search** for the symbol by name:
   ```bash
   orchard search --name "<user's description>"
   ```
2. **If the user only has a stack frame**, use `orchard_lookup_frame` first.
3. **Follow `status`, `diag`, and `next`** before assuming the miss is final.
4. **Confirm** the USR with the user when multiple direct matches remain.
5. **Look up** symbol metadata:
   ```bash
   orchard symbol --usr "<chosen USR>"
   ```
6. **Run the requested query** (callers, callees, impact, hierarchy).
   When 3+ queries are needed, use **pipe mode**.
7. **Synthesize** results into a human-readable summary. Highlight:
   - Confidence labels — which edges are compiler-verified vs inferred
   - How many dependents at each depth (d1 = WILL BREAK)
   - Risk level and what it means
   - ObjC semantic roles (notification wiring, delegate patterns)
   - Notification bridges — who registered → selector → event key → callback
   - Cross-language bridges (ObjC ↔ Swift)
   - Freshness vs coverage when the search path was ambiguous or stale
   - Execution boundaries (`call_style`, `execution_boundary`) when reasoning
     about crash threads, lifecycle races, callbacks, or worker dispatch
   - `source_scope` when a symbol's `file_path` is outside the current workspace
     root; warn that grep under cwd may not find that source

## Interpreting USR formats

| Format | Example |
|--------|---------|
| ObjC class | `c:objc(cs)MyViewController` |
| ObjC instance method | `c:objc(cs)MyViewController(im)viewDidLoad` |
| ObjC class method | `c:objc(cs)MyViewController(cm)sharedInstance` |
| ObjC property backing | `c:objc(cs)MyViewController@_myProperty` |
| Swift symbol | `s:So17OS_dispatch_queueC8DispatchE5label3qos:…` |

Swift USRs are mangled — always search by human-readable name, not USR.

## Important constraints

- **Read-only** (except `rename --no-dry-run`): the CLI queries the graph, never
  modifies it.  To rebuild, run `orchard ingest --project-dir <path>`.
- **No target_id needed**: USR alone provides unambiguous symbol identity.
- **Stale data warning**: if impact returns risk `critical`, re-run ingest.
- **DB discovery**: auto-walks up from cwd; `--db` overrides.
- **No exact layout from IndexStore**: do not use Orchard to assert C++ member
  byte offsets unless a future ABI/DWARF layout source is explicitly present.

## Maintenance action

`orchard_refresh_index` is a phase-1 maintenance action contract, not a normal
MCP tool. If Orchard recommends it in `next`, execute the project's Orchard
refresh command before drawing strong conclusions from a miss-path:

```bash
orchard ingest --project-dir <project-root>
```
