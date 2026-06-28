---
name: orchard
description: >
  Query the Orchard Apple Semantic Graph to analyze code relationships in
  indexed Xcode projects. Use this skill whenever the user asks about
  function callers or callees, impact / blast-radius analysis, type
  hierarchies, symbol lookups, code dependencies, "who calls X", "what
  does Y depend on", ObjC notification/delegate wiring, safe renaming,
  or wants to understand how iOS / macOS components relate to each other.
  Also use it when the user mentions "orchard" directly, or asks for a
  graph-based view of their Objective-C / Swift codebase. Every edge is
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

## Finding symbols (search)

```bash
orchard search --name "<text>" [--kind <kind>] [--language <swift|objc>] [--limit 20]
```

The `--name` flag does substring matching. Regex works too:

- Substring (default): `--name "release"` → `.*release.*`
- Exact match: `--name "^viewDidLoad$"`
- Prefix match: `--name "^MyClass.*"`

Results include `by_kind` grouping (field / method / class / protocol / …)
alongside a flat `results` list.

If ambiguous, ask the user which symbol they meant before running deeper queries.

## Query commands

### find_callers — Who calls this symbol?

```bash
orchard find_callers --usr "<USR>"
```

Each caller includes:
- `confidence`: `compiler-verified` (source_direct) or `inferred` (indexstore_relation_only)
- `provenance`: raw reason from the IndexStore edge
- `file_path` from Symbol table (line/col not persisted; use grep for precise locations)

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

### find_references — Incoming + outgoing in one call

```bash
orchard find_references --usr "<USR>"
```

### impact — Blast-radius analysis

```bash
orchard impact --usr "<USR>" [--max-depth 5]
```

Returns dependents grouped by depth and a risk level.  Includes **subtype
closure** (protocol conformers, subclasses) in d1.

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

```bash
# Show all notification chains:
orchard notification-graph

# Filter by notification name:
orchard notification-graph -n kNoti_LogoutForUI

# JSON output:
orchard notification-graph -f json
```

Shows poster → [notification] → callback chains.  During ingest,
Notification nodes and Posts/Observes edges are persisted to the graph.
Query them directly via Cypher for instant answers:

```cypher
-- Full chain: poster → notification → callback
MATCH (p:Symbol)-[:Posts]->(n:Notification)-[:Observes]->(cb:Symbol)
RETURN DISTINCT p.name, n.name, cb.name

-- Who observes this notification?
MATCH (n:Notification {name: 'kNoti_X'})-[o:Observes]->(cb:Symbol)
RETURN DISTINCT cb.name, o.selector
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
| `Observes` | N notifies callback C | derive/notification |
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
2. **Confirm** the USR with the user (show top matches, let them pick).
3. **Look up** symbol metadata:
   ```bash
   orchard symbol --usr "<chosen USR>"
   ```
4. **Run the requested query** (callers, callees, impact, hierarchy).
   When 3+ queries are needed, use **pipe mode**.
5. **Synthesize** results into a human-readable summary. Highlight:
   - Confidence labels — which edges are compiler-verified vs inferred
   - How many dependents at each depth (d1 = WILL BREAK)
   - Risk level and what it means
   - ObjC semantic roles (notification wiring, delegate patterns)
   - Cross-language bridges (ObjC ↔ Swift)

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
