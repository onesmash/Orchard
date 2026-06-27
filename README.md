# Orchard — Apple Semantic Graph

> Compiler-grade code intelligence for Apple platforms, built for AI agents.

Orchard builds a semantic code graph from Xcode's IndexStore — every edge is
compiler-verified, not heuristic. It provides call-graph analysis, impact
assessment, type hierarchies, and semantic search for Swift, Objective-C, C,
and C++ codebases via a CLI and MCP server.

## Features

- **Compiler-verified edges** — data source is Xcode IndexStore, so call
  relationships are ground truth, not regex approximations
- **MCP server** — runs as a long-lived subprocess for Claude Code / Claude
  Desktop, with 9 tools: search, callers, callees, references, impact,
  symbol metadata, type hierarchy, stats, and audit
- **Auto-discovery** — detects `.xcworkspace`/`.xcodeproj`, matches
  DerivedData, and locates the IndexStore automatically
- **Noise filtering** — excludes C++ operator overloads, logging macros, and
  stream helpers by default; inferred edges are opt-in
- **Impact analysis** — depth-grouped blast radius (d1 = direct callers /
  WILL BREAK, d2 = indirect / LIKELY AFFECTED, d3+ = transitive)
- **Cross-language bridges** — traces ObjC ↔ Swift call edges
- **Community detection** — Leiden algorithm for module boundary discovery
- **Execution flows** — automatic process detection from the call graph
- **Hybrid search** — BM25 + vector embeddings for semantic code search
- **One-shot setup** — `orchard setup` configures the MCP server, installs
  the Claude Code skill, downloads the embedding model, and injects the
  code-intelligence block into CLAUDE.md

## Quick Start

### Prerequisites

- Python >= 3.12
- An Xcode project with a recent build (generates IndexStore data)

### Install

```bash
# 方式一：从 git 安装为全局 CLI 工具
uv tool install git+ssh://git@git.zoom.us/ai-tools/orchard.git

# 方式二：clone + 本地开发和安装
git clone git@git.zoom.us:ai-tools/orchard.git
cd orchard
uv tool install -e .
```

### Index Your Project

```bash
# Auto-detects everything from the project directory
orchard ingest --project-dir /path/to/YourXcodeProject

# What happens:
# 1. Finds .xcworkspace / .xcodeproj → derives scheme/target
# 2. Matches DerivedData via Info.plist WorkspacePath
# 3. Locates the IndexStore
# 4. Ingests symbols, calls, contains, inherits, and implements edges
# 5. Writes the graph to <project>/.orchard/graph.db
```

### One-Shot Claude Code Integration

```bash
orchard setup
```

This configures everything Claude Code needs:
- ✅ MCP server entry in `settings.json`
- ✅ Orchard skill in `.claude/skills/orchard/`
- ✅ Embedding model download
- ✅ Code-intelligence block injected into `CLAUDE.md` / `AGENTS.md`

Use `--mcp`, `--skill`, `--model`, or `--claude-md` to install individual components.

## Usage

### CLI

```bash
# Find symbols by name (substring match)
orchard search --name "viewDidLoad" --kind method --language swift

# Who calls this symbol?
orchard find_callers --usr "s:MyClass::myMethod()"

# What does this symbol call?
orchard find_callees --usr "s:MyClass::myMethod()" --include-inferred

# Blast-radius analysis
orchard impact --usr "s:MyClass::myMethod()"

# Type hierarchy (superclasses, protocols, subclasses)
orchard hierarchy --usr "c:MyModule::MyClass"

# Symbol metadata
orchard symbol --usr "s:MyClass::myMethod()"

# Database statistics
orchard stats

# Module coverage audit
orchard audit --project-dir /path/to/project
```

### MCP Server

The MCP server is designed to be launched by Claude Code as a subprocess:

```bash
orchard-mcp [--db /path/to/graph.db]
```

It exposes 9 tools:

| Tool | Description |
|------|-------------|
| `orchard_search` | Search symbols by name or list class methods |
| `orchard_find_callers` | Find all callers of a symbol (multi-hop) |
| `orchard_find_callees` | Find all callees of a symbol (multi-hop) |
| `orchard_find_references` | Incoming + outgoing references |
| `orchard_impact` | Blast-radius analysis with depth groups |
| `orchard_symbol` | Symbol metadata (name, kind, language, module) |
| `orchard_hierarchy` | Type hierarchy (superclasses, protocols, conformers) |
| `orchard_stats` | Database statistics |
| `orchard_audit` | Module coverage report with Xcode target gap detection |

### Signal Filtering

| Parameter | Default | Effect |
|-----------|---------|--------|
| `include_noise` | `false` | Show C++ operators & logging helpers |
| `include_inferred` | `false` | Show compiler-inferred edges |

By default, only source-level call evidence is returned — compiler-verified
call sites, not heuristics.

### DB Discovery

Commands find the database automatically:

1. `--db <path>` flag
2. `ORCHARD_DB_PATH` environment variable
3. Walk up from cwd → first `.orchard/graph.db` found (project-level)
4. `~/.orchard/graph.db` (global fallback)

You rarely need to pass `--db` explicitly — just run queries from anywhere
under the project directory.

## Architecture

```
Xcode IndexStore                    ┌─────────────────────────┐
（compiler artifacts）               │       MCP Server         │
         │                          │  (orchard-mcp, stdio)    │
         ▼                          │  search / callers /      │
┌─────────────────┐                 │  callees / impact /      │
│  ingest/         │                │  hierarchy / stats /     │
│  indexstore.py   │──▶ graph.db ──▶│  audit / references /    │
│  symbolgraph.py  │   (Ladybug/    │  symbol                  │
└─────────────────┘    DuckDB)      └─────────────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
   ┌──────────┐          ┌────────────┐         ┌────────────┐
   │ derive/  │          │  search/    │         │  query/    │
   │ arch     │          │  hybrid     │         │  lookup    │
   │ bridge   │          │  embedder   │         │  noise     │
   │ community│          │  chunker    │         │  filter    │
   │ process  │          └────────────┘         └────────────┘
   │ mro      │
   └──────────┘
```

### Data Pipeline

The ingest process runs a phase pipeline:

1. **IndexStore** — parse `recordName.Unit` files → extract symbol declarations, occurrences, and relations
2. **Symbol Graph** — parse `.swiftsymbolgraph` files for Swift interface data
3. **Build** — create `BuildSnapshot` and `Target` nodes with source-root anchoring
4. **Normalize** — USR identity normalization
5. **Graph** — insert symbols and edges into Ladybug/DuckDB graph
6. **Derive** — post-ingest: community detection (Leiden), process detection, bridge edges, MRO, architecture

### Key Modules

| Module | Purpose |
|--------|---------|
| `orchard.cli` | CLI entry point with all query commands |
| `orchard.server` | MCP server (stdio transport) |
| `orchard.setup` | One-shot Claude Code configuration |
| `orchard.ingest` | IndexStore & Symbol Graph parsing |
| `orchard.graph` | Ladybug/DuckDB schema & connection |
| `orchard.handlers` | Query logic for each MCP tool |
| `orchard.derive` | Community detection, process detection, bridges |
| `orchard.search` | Embeddings & hybrid search |
| `orchard.query` | Graph lookup helpers & noise filter |
| `orchard.pipeline` | Phase-based ingest pipeline runner |
| `orchard.build` | Xcode project/build discovery |
| `orchard.validation` | Index freshness checks |

## Requirements

- Python >= 3.12
- [igraph](https://igraph.org/) — graph algorithms (Leiden community detection)
- `ladybug` — DuckDB-powered graph database (internal)
- `leidenalg` — Leiden community detection
- `llama-cpp-python` — local embeddings
- `mcp` — MCP Python SDK

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Build wheel
uv build
```

## License

Proprietary — Zoom Video Communications, Inc.

## See Also

- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/)
- [igraph — graph algorithms library](https://igraph.org/)
- [Leiden algorithm for community detection](https://github.com/vtraag/leidenalg)
- [llama.cpp — local LLM inference](https://github.com/ggml-org/llama.cpp)
