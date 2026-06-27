---
name: orchard
description: >
  Query the Orchard Apple Semantic Graph to analyze code relationships in
  indexed Xcode projects. Use this skill whenever the user asks about
  function callers or callees, impact / blast-radius analysis, type
  hierarchies, symbol lookups, code dependencies, "who calls X", "what
  does Y depend on", "trace this call", "find references to", or wants to
  understand how iOS / macOS components relate to each other.  Also use
  it when the user mentions "orchard" directly, asks for a graph-based
  view of their ObjC / Swift codebase, or wants compiler-level call-graph
  data from IndexStore.  The skill works against a per-project graph
  database at ``<project>/.orchard/graph.db`` built from Xcode IndexStore
  data — edges are compiler-verified, not heuristic.
---

# Orchard — Semantic Graph CLI

The `orchard` CLI queries a semantic code graph (Ladybug/KuzuDB) built from
Xcode IndexStore data.  The database lives at ``<project>/.orchard/graph.db``
(GitNexus convention) and is auto-discovered by walking up from the current
directory.

## Installation

```bash
# Install from git
pip install git+https://github.com/xxx/orchard2.git

# Or install from local source
pip install -e /path/to/orchard2

# One-time setup: install skill + MCP config
orchard setup

# Verify
orchard --help
```

## Ingest — building the graph

The ingest command auto-detects the IndexStore from an Xcode project:

```bash
# Zero-parameter: auto-detects everything from project directory
orchard ingest --project-dir /path/to/YourProject

# What it does:
# 1. Finds .xcworkspace/.xcodeproj → derives target name
# 2. Matches DerivedData via info.plist WorkspacePath
# 3. Auto-discovers IndexStore path
# 4. Defaults source-root to project directory
# 5. Writes DB to <project>/.orchard/graph.db

# Manual override (compatible with old workflow):
orchard ingest --index-store /path/to/Index.noindex/DataStore --db /path/to/graph.db
```

After ingest, the DB is at ``<project>/.orchard/graph.db`` and queries from
any subdirectory automatically find it.

## DB discovery

Commands find the database automatically with this priority:

1. `--db <path>` flag
2. `ORCHARD_DB_PATH` environment variable
3. Walk up from cwd → first `.orchard/graph.db` found (project-level)
4. `~/.orchard/graph.db` (global fallback)

This means you **rarely need `--db`** — just run queries from anywhere under
the project.

## Finding symbols (search)

Every query needs a USR (Unified Symbol Resolution string). Find USRs with
the `search` command:

```bash
orchard search --name "<text>" [--target <Module>] [--kind <kind>] \
  [--language <swift|objc|c>] [--limit 20]
```

The `--name` flag does **substring matching by default** — `--name "viewDidLoad"`
matches any symbol whose name contains "viewDidLoad".  For regex matching,
use Cypher syntax explicitly:

- Substring (default): `--name "release"` → auto-wraps to `.*release.*`
- Exact match: `--name "^viewDidLoad$"`
- Prefix match: `--name "^MyClass.*"`

Filters help narrow large result sets:
- `--kind method` / `--kind class` / `--kind protocol`
- `--language swift` / `--language objc`
- `--target <Module>` — only symbols in that module

Present the top matches to the user when there are multiple candidates.
If ambiguous, ask which symbol they meant before running deeper queries.

## Query commands

### find_callers — Who calls this symbol?

```bash
orchard find_callers --usr "<USR>" --target <Module>
```

Returns caller objects with `usr`, `name`, `module`, `kind`, `language`,
`reason` (source provenance), and `owner` (containing class/struct/extension).
Present as a table.

**Signal filtering** — two independent controls:

| Flag | Default | What it controls |
|------|---------|-----------------|
| (no flag) | on | C++ operators, logging helpers hidden |
| `--include-noise` | off | Show C++ operators/helpers in results |
| (no flag) | on | Compiler-inferred edges hidden |
| `--include-inferred` | off | Show `indexstore_relation_only` edges |

```bash
# Default — clean business-logic view
orchard find_callers --usr "<USR>" --target <Module>

# Show everything (C++ operators + inferred edges)
orchard find_callers --usr "<USR>" --target <Module> \
  --include-noise --include-inferred
```

**Note on data quality**: IndexStore records compiler-verified call sites.
Unlike AST-based tools (e.g. GitNexus), orchard edges are not "maybe true"
heuristics — the compiler confirmed every call.  C++ operators and
`indexstore_relation_only` edges are real calls that are simply uninteresting
for business-logic tracing; they're hidden by default, not discarded.

### find_callees — What does this symbol call?

```bash
orchard find_callees --usr "<USR>" --target <Module>
```

Same arguments and filtering as `find_callers`.  Returns callee objects with
`reason` provenance.

### impact — Blast-radius analysis (includes subtype closure + freshness)

```bash
orchard impact --usr "<USR>" --target <Module>
```

Returns dependents grouped by depth and a risk level.  Now includes:
- **Subtype conformers** via Inherits/ConformsTo/Extends/Implements closure
- **Freshness annotations** in `open_gaps` when index may be stale

**Depth groups:**

| Depth | Meaning |
|-------|---------|
| `d1` | WILL BREAK — direct callers, subtypes, protocol conformers |
| `d2` | LIKELY AFFECTED — callers of callers |
| `d3+` | MAY NEED TESTING — transitive dependents |

**Risk levels:**

| Level | Condition |
|-------|-----------|
| `critical` | Graph index is stale — re-ingest before trusting results |
| `high` | ≥10 direct dependents, or cross-language bridges with ≥4 dependents |
| `medium` | 4–9 direct dependents |
| `low` | <4 direct dependents |

**Always warn the user** before proposing changes to HIGH or CRITICAL risk
symbols. For HIGH risk, suggest extra testing. For CRITICAL, suggest
re-running `orchard ingest` first.

### symbol — Metadata for a single symbol

```bash
orchard symbol --usr "<USR>" --target <Module>
```

### hierarchy — Type hierarchy

```bash
orchard hierarchy --usr "<USR>" --target <Module>
```

### stats — Database overview

```bash
orchard stats
```

Shows counts: Symbol, Calls, Contains, Inherits, Implements, Extends.

## Pipe mode — batch queries in one process

**Prefer pipe when running 3+ queries.** One DB connection, no cold start.

```bash
echo '{"cmd":"search","args":{"name":"viewDidLoad","limit":5}}
{"cmd":"find_callers","args":{"usr":"<USR>","target_id":"<Module>"}}
{"cmd":"find_callees","args":{"usr":"<USR>","target_id":"<Module>",
  "include_noise": true, "include_inferred": true}}
{"cmd":"impact","args":{"usr":"<USR>","target_id":"<Module>"}}' \
  | orchard pipe
```

Each output line: `{"cmd":"...", "ok":true, "data":{...}}`.
Signal-filtering args (`include_noise`, `include_inferred`) default to false.

## MCP server — zero-latency for Claude Code

When the MCP server is configured (run `orchard setup` to auto-configure), all
orchard tools are available as MCP tools with **session-scoped DB connection**
(~21ms per call vs ~170ms CLI cold start).  The skill should prefer MCP tools
when available; fall back to CLI pipe for batch queries.

## Typical workflow for a user question

1. **Search** for the symbol by name:
   ```bash
   orchard search --name "<user's description>"
   ```
2. **Confirm** the USR with the user (show top matches, let them pick).
3. **Look up** symbol metadata:
   ```bash
   orchard symbol --usr "<chosen USR>" --target <Module>
   ```
4. **Run the requested query** (callers, callees, impact, hierarchy).
   When 3+ queries are needed, use **pipe mode**.
5. **Synthesize** results into a human-readable summary. Highlight:
   - How many dependents at each depth (d1 = WILL BREAK)
   - Risk level and what it means
   - Subtype closure results (protocol conformers, subclasses)
   - Freshness warnings from `open_gaps`
   - Cross-language bridges (ObjC ↔ Swift callers)

## Interpreting USR formats

| Format | Example |
|--------|---------|
| ObjC class | `c:objc(cs)MyViewController` |
| ObjC instance method | `c:objc(cs)MyViewController(im)viewDidLoad` |
| ObjC class method | `c:objc(cs)MyViewController(cm)sharedInstance` |
| Swift symbol | `s:So17OS_dispatch_queueC8DispatchE5label3qos:...` |
| C function | `c:@F@_Block_release` |

Swift USRs are mangled. The `search` command works regardless of language
— always search by human-readable name, not USR.

## MCP tool parameters

Prefer MCP tools when the server is running.  Key parameters:

| Tool | Extra params |
|------|-------------|
| `orchard_find_callers` | `include_noise` (bool), `include_inferred` (bool), `depth` (int) |
| `orchard_find_callees` | `include_noise` (bool), `include_inferred` (bool), `depth` (int) |
| `orchard_search` | `name`, `target`, `kind`, `language`, `limit` |

## Important constraints

- **Read-only**: The CLI only queries the graph, never modifies it.
  To rebuild, the user must run `orchard ingest --project-dir <path>`.
- **Stale data warning**: If `impact` returns risk `critical`, the graph
  index is out of date. Suggest re-running `orchard ingest`.
- **DB discovery**: No `--db` needed — walks up from cwd to find
  `.orchard/graph.db`. Override with `--db` if needed.
- **Data quality**: IndexStore edges are compiler-verified — every CALLS
  edge represents a real call the compiler confirmed.  Unlike AST-based
  tools, orchard does not need heuristic filtering for "false" edges.
  The built-in noise filter only removes C++ operators/helpers that are
  technically correct but uninteresting for business-logic tracing.
- **Dynamic dispatch**: ObjC `release`/`retain`/`alloc`-style selectors
  may have duplicate callers due to IndexStore's dynamic dispatch
  limitations.
- **Schema migration**: Existing databases are auto-migrated on first
  `init_schema()` call (ALTER TABLE ADD with duplicate detection).
