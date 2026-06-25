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
  codebase. The skill works against a pre-built Ladybug graph database
  produced by the orchard ingest pipeline from Apple IndexStore data.
---

# Orchard — Semantic Graph CLI

The `orchard` CLI queries a pre-built semantic code graph (Ladybug/KuzuDB)
containing symbols, call edges, and structural relationships (Contains,
Inherits, Implements, Extends) extracted from Apple IndexStore data.

## Quick check

Always verify the CLI responds before running queries:

```bash
uv run orchard --help
```

The default database is `/tmp/orchard-final/graph.db` (Zoom iOS full build,
~228k symbols). Override with `--db <path>` on every command.

## Finding symbols (search)

Every query needs a USR (Unified Symbol Resolution string). Find USRs with
the `search` command before running other queries:

```bash
uv run orchard search --name "<regex>" [--target <Module>] [--kind <kind>] \
  [--language <swift|objc|c>] [--limit 20] [--db <path>]
```

The `--name` flag takes a case-sensitive Cypher regex. Common patterns:

- Exact match: `--name "^viewDidLoad$"`
- Prefix match: `--name "^ZMZoom.*"`
- Substring: `--name "release"`
- Swift symbol: `--name "init\\("`

Filters help narrow large result sets:

- `--kind method` — only methods
- `--kind class` — only classes
- `--language swift` — only Swift symbols
- `--target Zoom` — only symbols in the Zoom module

Present the top matches to the user when there are multiple candidates.
If the user's description is ambiguous, run search and ask which symbol
they meant before running deeper queries.

## Query commands

### find_callers — Who calls this symbol?

```bash
uv run orchard find_callers --usr "<USR>" --target <Module> [--db <path>]
```

Returns a JSON array of caller objects, each with `usr`, `name`, `module`,
`kind`, `language`, and `owner` (the containing class/struct/extension).

Present results as a table or grouped list. If a symbol has many callers,
highlight the most interesting ones (e.g. different modules, UI entry
points).

### find_callees — What does this symbol call?

```bash
uv run orchard find_callees --usr "<USR>" --target <Module> [--db <path>]
```

Returns a JSON array of callee objects. Useful for understanding what a
function depends on internally.

### impact — Blast-radius analysis

```bash
uv run orchard impact --usr "<USR>" --target <Module> [--db <path>]
```

Returns dependents grouped by depth and a risk level. This answers "what
breaks if I change this symbol?".

**Depth groups:**
| Depth | Meaning |
|-------|---------|
| `d1` | WILL BREAK — direct callers, subclasses, protocol conformers |
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
uv run orchard symbol --usr "<USR>" --target <Module> [--db <path>]
```

Returns `name`, `language`, `kind`, `module`, `file_path`, `signature`,
`access_level`. Use this to confirm you have the right USR before running
impact or callers queries.

### hierarchy — Type hierarchy

```bash
uv run orchard hierarchy --usr "<USR>" --target <Module> [--db <path>]
```

Returns superclasses, protocols, and subclasses/conformers. Use this to
understand where a class sits in the inheritance tree.

### stats — Database overview

```bash
uv run orchard stats [--db <path>]
```

Shows counts: Symbol, Calls, Contains, Inherits, Implements, Extends.
Run this first if the user asks "how big is the graph?" or "what's in the
database?".

## Typical workflow

1. **Search** for the symbol by name:
   ```bash
   uv run orchard search --name "<user's description>" --db /tmp/orchard-final/graph.db
   ```
2. **Confirm** the USR with the user (show top matches, let them pick).
3. **Look up** symbol metadata:
   ```bash
   uv run orchard symbol --usr "<chosen USR>" --target <Module> --db /tmp/orchard-final/graph.db
   ```
4. **Run the requested query** (callers, callees, impact, hierarchy).
5. **Synthesize** results into a human-readable summary. Highlight:
   - How many dependents at each depth
   - Risk level and what it means
   - Cross-language bridges (ObjC ↔ Swift callers)
   - Any surprising or high-impact findings

## Interpreting USR formats

| Format | Example |
|--------|---------|
| ObjC class | `c:objc(cs)ZMMeetingViewController` |
| ObjC instance method | `c:objc(cs)ZMMeetingViewController(im)viewDidLoad` |
| ObjC class method | `c:objc(cs)ZMMeetingViewController(cm)sharedInstance` |
| Swift symbol | `s:So17OS_dispatch_queueC8DispatchE5label3qos:...` |
| C function | `c:@F@_Block_release` |
| C macro | `c:@macro@Block_release` |

Swift USRs are mangled. The `search` command works regardless of language
— always search by human-readable name, not USR.

## Important constraints

- **Read-only**: The CLI only queries the graph, it never modifies it.
  To rebuild the graph, the user must run `orchard ingest` separately.
- **Stale data warning**: If `impact` returns risk `critical`, the graph
  index is out of date. Tell the user they should re-ingest before making
  changes based on stale data.
- **Dynamic dispatch**: ObjC `release`/`retain`/`alloc`-style selectors
  may have duplicate callers due to IndexStore's dynamic dispatch
  limitations. Mention this when results look inflated for ObjC methods.
- **Filtered scope**: The graph was built with a `--source-root` filter,
  so SDK/system symbols (UIKit, Foundation, etc.) are typically absent
  unless they were compiled as part of the target.
