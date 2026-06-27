---
name: orchard
description: >
  Query the Orchard Apple Semantic Graph to analyze code relationships in
  indexed Xcode projects. Use this skill whenever the user asks about
  function callers or callees, impact / blast-radius analysis, type
  hierarchies, symbol lookups, code dependencies, "who calls X", "what
  does Y depend on", or wants to understand how iOS / macOS components
  relate to each other. Also use it when the user mentions "orchard"
  directly, or asks for a graph-based view of their Objective-C / Swift
  codebase. The skill works against a per-project graph database at
  ``<project>/.orchard/graph.db`` built from Apple IndexStore data.
---

# Orchard — Semantic Graph CLI

The `orchard` CLI queries a semantic code graph (Ladybug/KuzuDB) built from
Xcode IndexStore data.  The database lives at ``<project>/.orchard/graph.db``
and is auto-discovered by walking up from the current directory.

## Installation

```bash
pip install -e /path/to/orchard2
orchard setup    # install MCP config + skill
orchard --help
```

## Ingest — building the graph

```bash
# Auto-detect everything from project directory
orchard ingest --project-dir /path/to/YourProject

# Multi-target: comma-separated (e.g., app target + framework target)
orchard ingest --project-dir . --target Zoom,iOSLogin

# With SymbolGraph JSON for Swift type/protocol relationships
orchard ingest --project-dir . --symbolgraph path/to/symbols.json

# Incremental: only ingest changed files
orchard ingest --project-dir . --incremental

# Manual override
orchard ingest --index-store /path/to/Index.noindex/DataStore --db /path/to/graph.db
```

After ingest, the DB is at `<project>/.orchard/graph.db`. Queries from any
subdirectory automatically find it.  Symbol IDs are USR-based (sourcekit-lsp
convention) — no target prefix.

## DB discovery

1. `--db <path>` flag
2. `ORCHARD_DB_PATH` environment variable
3. Walk up from cwd → first `.orchard/graph.db`
4. `~/.orchard/graph.db` (global fallback)

## Finding symbols (search)

```bash
orchard search --name "<text>" [--kind <kind>] [--language <lang>] \
  [--file <path-substring>] [--limit 20]

# Find all methods of a class
orchard search --class <ClassName>

# Filter by file path (substring match)
orchard search --name "login" --file "ZMAppLoginHelper.mm"
```

- `--name`: substring match by default. Regex metacharacters used as-is.
- `--class`: find a class/struct/enum/protocol and list all its methods.
- `--file`: filter by `file_path` substring.
- `--kind` / `--language`: narrow results.

## Query commands

**All commands work without `--target`** — USRs are globally unique. The
`--target` flag is accepted for backward compatibility but ignored.

### find_callers — Who calls this symbol?

```bash
orchard find_callers --usr "<USR>" [--depth N] \
  [--relation-types Calls,Inherits,Implements] [--include-noise]
```

Class/struct/enum/protocol USRs auto-expand to all their methods. Each caller
is annotated with `via_method`.  Use `--depth N` for multi-hop BFS traversal.
Use `--relation-types` to traverse non-Calls edges (Inherits, Implements, etc.).
`--include-noise` shows unfiltered C++ operators.

### find_callees — What does this symbol call?

```bash
orchard find_callees --usr "<USR>" [--depth N] \
  [--relation-types Calls,Inherits,Implements] [--include-noise]
```

Same auto-expand and multi-hop support. Callees are grouped by USR with
`calling_methods` list. C++ operators are filtered by default.

### find_references — Incoming and outgoing references

```bash
orchard find_references --usr "<USR>"
```

Returns `outgoing` (what this symbol calls) and `incoming` (who calls this symbol).

### impact — Blast-radius analysis

```bash
orchard impact --usr "<USR>"
```

Returns dependents grouped by depth and a risk level.

**Depth groups:**

| Depth | Meaning |
|-------|---------|
| `d1` | WILL BREAK — direct callers, subtypes, protocol conformers |
| `d2` | LIKELY AFFECTED — callers of callers |
| `d3+` | MAY NEED TESTING — transitive dependents |

**Risk levels:**

| Level | Condition |
|-------|-----------|
| `critical` | Graph index is stale — re-ingest first |
| `high` | ≥10 direct dependents, or cross-language bridges with ≥4 |
| `medium` | 4–9 direct dependents |
| `low` | <4 direct dependents |

### symbol — Metadata for a single symbol

```bash
orchard symbol --usr "<USR>"
```

### hierarchy — Type hierarchy

```bash
orchard hierarchy --usr "<USR>"
```

Returns `parents`, `protocols`, and `children`.

### audit — Module coverage report

```bash
orchard audit [--project-dir <path>] [--format table|json]
```

Shows per-module symbol counts by kind. Detects unexpected gaps when a
framework target has <100 symbols. Compares graph modules against Xcode
workspace targets when `--project-dir` is given.

### stats — Database overview

```bash
orchard stats
```

Shows counts: Symbol, Calls, Contains, Inherits, Implements, Extends.

## Pipe mode — batch queries

**Prefer pipe when running 3+ queries.** One DB connection, no cold start.

```bash
echo '{"cmd":"search","args":{"name":"viewDidLoad","limit":5}}
{"cmd":"find_callers","args":{"usr":"<USR>","depth":2,"relation_types":["Calls"]}}
{"cmd":"find_callees","args":{"usr":"<USR>","depth":3,"include_noise":false}}' \
  | orchard pipe
```

Each output line: `{"cmd":"...", "ok":true, "data":{...}}`.

## MCP server — zero-latency for Claude Code

`orchard setup` auto-configures all orchard tools as MCP tools with
session-scoped DB connection (~21ms per call vs ~170ms CLI cold start).
Prefer MCP tools when available; fall back to CLI pipe for batch queries.

## Typical workflow

1. **Search** for the symbol by name:
   ```bash
   orchard search --name "<user's description>"
   ```
2. **Confirm** the USR with the user (show top matches).
3. **Look up** symbol metadata:
   ```bash
   orchard symbol --usr "<chosen USR>"
   ```
4. **Run the requested query** (callers, callees, impact, hierarchy, references).
   For 3+ queries, use **pipe mode**.
   For flow tracing, use `--depth 3` or higher.
5. **Synthesize** results. Highlight:
   - How many dependents at each depth (d1 = WILL BREAK)
   - Risk level
   - Cross-language bridges (ObjC ↔ Swift, ObjC ↔ C++)
   - Multi-hop reachability via `--depth`
   - Cross-edge traversal via `--relation-types`

## USR formats

| Format | Example |
|--------|---------|
| ObjC class | `c:objc(cs)MyViewController` |
| ObjC instance method | `c:objc(cs)MyViewController(im)viewDidLoad` |
| ObjC class method | `c:objc(cs)MyViewController(cm)sharedInstance` |
| Swift symbol | `s:So17OS_dispatch_queueC8DispatchE5label3qos:...` |
| C function | `c:@F@_Block_release` |

Swift USRs are mangled. Always search by human-readable name, not USR.

## Constraints

- **Read-only**: queries only, never modifies the graph. Rebuild via `orchard ingest`.
- **Stale data**: if `impact` returns risk `critical`, suggest re-ingest.
- **IndexStore coverage**: only symbols from compiled targets are indexed. Pre-built
  frameworks linked into the app don't appear. Use `--target` with the framework
  target name during ingest, or `--symbolgraph` for Swift type-level data.
- **DB discovery**: auto-walks from cwd. Override with `--db`.
- **Dynamic dispatch**: ObjC `release`/`retain` duplicates may appear.
- **USR-only IDs**: symbol identity follows sourcekit-lsp convention. Re-ingest
  after upgrading from pre-USR-only versions.
