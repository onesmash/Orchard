---
name: orchard-cli
description: "Use when the user needs to run Orchard CLI commands directly in a terminal: install Orchard, run `orchard setup`, build or refresh a project graph with `orchard ingest`, inspect DB coverage with `orchard stats` or `orchard audit`, batch graph queries with `orchard pipe`, inspect notification or UIKit target-action wiring, or do CLI-first symbol lookup / rename workflows. Make sure to use this skill whenever the user asks for the exact Orchard command to run, says the MCP server or graph is stale/missing, wants to configure Orchard on a machine, or needs a terminal-only workflow instead of MCP tools."
---

# Orchard CLI Commands

Use this skill when the task is primarily about **running Orchard from the
terminal**, not just interpreting graph results. Favor exact commands,
practical flags, and troubleshooting steps that help the user get Orchard
working end to end.

The `orchard` CLI covers four common jobs:

1. install and configure Orchard for Claude/Codex
2. build or refresh a graph from Xcode IndexStore
3. run direct graph queries from the terminal
4. inspect or repair local Orchard state

## Quick Principles

- Prefer the `orchard` executable directly.
- Run `orchard setup` when the user wants agent integration.
- Run `orchard ingest --project-dir <path>` when the graph is missing, stale,
  or after meaningful code/build changes.
- Prefer CLI when the user explicitly asks for shell commands, terminal steps,
  or a non-MCP workflow.
- After the graph is ready, hand off exploration/debugging/refactoring tasks to
  more specialized Orchard skills if available.

## Install

### Global tool install

```bash
uv tool install git+ssh://git@git.zoom.us/ai-tools/orchard.git
```

### Local dev install

```bash
git clone git@git.zoom.us:ai-tools/orchard.git
cd orchard
uv tool install -e .
```

Use the local editable install when the user is developing Orchard itself.

## Setup

### One-shot agent setup

```bash
orchard setup
```

This installs the typical agent-side pieces:

- MCP server configuration
- Orchard skills
- embedding model
- CLAUDE.md / AGENTS.md Orchard block

### Partial setup

```bash
orchard setup --mcp
orchard setup --skill
orchard setup --model
orchard setup --claude-md --project-dir /path/to/project
```

Use the partial flags when the user wants to fix one broken component instead
of reinstalling everything.

## Ingest

### Default ingest

```bash
orchard ingest --project-dir /path/to/YourXcodeProject
```

This is the main "refresh Orchard" command. It auto-detects:

- `.xcworkspace` / `.xcodeproj`
- matching DerivedData
- IndexStore location
- default graph DB path

It writes the graph to:

```text
<project>/.orchard/graph.db
```

### Explicit ingest

```bash
orchard ingest \
  --project-dir /path/to/project \
  --index-store /path/to/DerivedData/.../Index.noindex/DataStore \
  --target Zoom
```

Use explicit flags when auto-detection fails, the wrong target is selected, or
the user already knows the exact IndexStore path.

### Full vs incremental

```bash
orchard ingest --project-dir /path/to/project
orchard ingest --project-dir /path/to/project --full
```

- default mode is incremental
- use `--full` when the graph looks inconsistent or the user suspects stale
  incremental state

## Query Commands

Use direct CLI queries when the user wants terminal output, shell automation,
or does not have MCP tools available.

### Search

```bash
orchard search --name "viewDidLoad"
orchard search --name "^MyClass.*" --kind method --language swift --limit 20
```

Use search first when the user has a human-readable symbol name instead of a
USR.

### Symbol / callers / callees / references / hierarchy

```bash
orchard symbol --usr "<USR>"
orchard find_callers --usr "<USR>"
orchard find_callees --usr "<USR>"
orchard find_references --usr "<USR>"
orchard hierarchy --usr "<USR>"
```

Use these after a search has resolved the exact symbol identity.

### Impact

```bash
orchard impact --usr "<USR>"
```

Use before edits to understand blast radius from the terminal.

### Notification wiring

```bash
orchard notification-graph
orchard notification-graph -n kNoti_LogoutForUI
orchard notification-graph -f json
```

Use this when the user is tracing NSNotificationCenter behavior or wants a
notification-centric view instead of a plain caller/callee list.

### UIKit target-action wiring

```bash
orchard target-action-graph
orchard target-action-graph -a onToggle:
orchard target-action-graph -c MyViewController
orchard target-action-graph -f json
```

Use this when the user is tracing `addTarget:action:forControlEvents:` style
UIKit wiring or wants a binding-centric view instead of relying on static
callers alone.

## Pipe Mode

When the user needs several related queries in one shell command, prefer
`orchard pipe`:

```bash
echo '{"cmd":"search","args":{"name":"viewDidLoad","limit":5}}
{"cmd":"symbol","args":{"usr":"<USR>"}}
{"cmd":"find_callers","args":{"usr":"<USR>"}}' | orchard pipe
```

Use pipe mode for:

- shell scripts
- batch diagnostics
- repeated terminal workflows
- cases where 3+ Orchard queries belong together

## Maintenance Commands

### Stats

```bash
orchard stats
```

Use this to confirm the database exists and inspect high-level graph counts.

### Audit

```bash
orchard audit --project-dir /path/to/project
```

Use audit when the user wants module coverage information or suspects some
targets/frameworks were not indexed well.

### Rename

```bash
orchard rename --usr "<USR>" --new-name "<newName>"
orchard rename --usr "<USR>" --new-name "<newName>" --no-dry-run
```

Always start with the dry run. Use the real rename only after reviewing the
preview.

## DB Discovery

Most commands auto-discover the graph DB in this order:

1. `--db <path>`
2. `ORCHARD_DB_PATH`
3. walk up from cwd to `.orchard/graph.db`
4. `~/.orchard/graph.db`

This means the user usually does **not** need to pass `--db` manually if they
run commands somewhere under the project root.

Important distinction:

- `--db` is Orchard's graph database
- `--index-store` is Xcode's IndexStore data source

Do not confuse a DerivedData path with the Orchard DB path.

## Typical CLI Workflows

### "Set up Orchard on this machine"

```bash
uv tool install git+ssh://git@git.zoom.us/ai-tools/orchard.git
orchard setup
```

### "Refresh the graph for this project"

```bash
orchard ingest --project-dir .
```

### "The search seems stale or wrong"

```bash
orchard stats
orchard ingest --project-dir . --full
```

### "Give me terminal-only graph answers"

```bash
orchard search --name "SomeSymbol"
orchard symbol --usr "<USR>"
orchard find_callers --usr "<USR>"
```

### "Why does this action method have no caller?"

```bash
orchard search --name "onToggle:"
orchard find_callers --usr "<USR>"
orchard target-action-graph -a onToggle:
```

Use this flow when a UIKit callback is triggered by runtime binding rather than
an ordinary static call edge. `find_callers` may show no direct caller while
`target-action-graph` reveals the concrete binding records.

### "Why does this notification callback have no caller?"

```bash
orchard search --name "onMyNotesPageRefreshed:"
orchard find_callers --usr "<USR>"
orchard notification-graph -n kNoti_MyNotes_PageRefreshed -f json
```

Use this flow when an ObjC callback is reached through
`NSNotificationCenter` wiring rather than an ordinary static call edge.
`find_callers` may show no direct caller while `notification-graph` reveals
the poster -> observer -> callback chain.

## Troubleshooting

- **`orchard: command not found`**: install Orchard with `uv tool install ...`
  or verify the tool is on `PATH`.
- **No graph found**: run `orchard ingest --project-dir <project-root>`.
- **Wrong project DB selected**: pass `--db` explicitly or run from under the
  intended project directory.
- **Auto-detection picks the wrong target / DerivedData**: pass `--target` and
  `--index-store` explicitly.
- **Graph still looks stale after ingest**: rerun with `--full`.
- **Notification callback has no static caller**: inspect
  `orchard notification-graph` before concluding it is unused.
- **Action callback has no static caller**: inspect
  `orchard target-action-graph` before concluding it is unused.
- **Need several CLI queries in one go**: use `orchard pipe` instead of many
  separate shell invocations.

## After the CLI Setup/Refresh Work

Once the graph is healthy, use other Orchard workflows for higher-level tasks:

- code understanding / architecture exploration
- crash-frame follow-up from a resolved symbol
- impact analysis before edits
- guided graph-based debugging
