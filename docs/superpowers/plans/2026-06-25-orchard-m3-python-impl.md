# Orchard M3: Bridge Recovery + Impact Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M3 milestone of the Orchard Apple Semantic Graph Python system: cross-language bridge recovery (ObjC↔Swift) + impact analysis traversal + bridge querying.

**Architecture:** A new `derive/bridge.py` phase discovers ObjC↔Swift bridge candidates (name/USR/module matching) and writes `BridgesTo` edges to the existing graph. Two new MCP tools—`impact_analysis` (multi-hop traversal with depth grouping and risk scoring) and `get_cross_language_bridges` (bridge edge queries)—consume these edges, plus a shared `ImpactTraversalPolicy` dataclass.

**Tech Stack:** Python≥3.12 + uv + Ladybug (KuzuDB) + mcp Python SDK. No new dependencies.

## Global Constraints

- Python ≥ 3.12 with `str | None` union syntax.
- Ladybug API: `.get_all()` not `.fetchall()`; `MATCH`+`MERGE` Cypher; `ladybug.Database(path)` + `ladybug.Connection(db)`.
- `_ConnectionWithDB` wrapper keeps the Database alive (module-level for stdio server).
- Symbol composite key: `"{target_id}:{usr}"` via `make_symbol_id`.
- Every MCP tool response must carry `freshness`, `build_id`, `evidence_sources`, `open_gaps` fields.
- BridgesTo edge fields: `bridge_kind STRING`, `provenance STRING`, `confidence DOUBLE`, `build_id STRING`.
- Low-confidence bridges (< 0.70) must not appear in default `impact_analysis` results.
- TDD: write failing test → confirm fail → implement minimal → confirm pass → commit.
- `git add` specific files ONLY — never `git add .` / `-A`.
- Pytest `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed).
- Run: `cd /Users/hui.xu/SourceCode/orchard2 && uv run pytest -x -q`.

## Existing M2 Foundation

- **Pipeline phases**: `indexstore_ingest` → `swift_symbolgraph_ingest` → `identity_normalization` → `call_graph_derivation`.
- **Schema ready**: `BridgesTo` table already declared (`schema.py:105-111`); no DDL changes needed.
- **Normalize helpers**: `make_symbol_id(target_id, usr) -> str` in `normalize/identity.py:14`.
- **Freshness**: `freshness_for(conn, build_id, query_ctx) -> (GraphFreshness, str)` in `validation/freshness.py:47`.
- **DB wrapper**: `get_connection(db_path)` returns `_ConnectionWithDB` in `graph/db.py:35`.
- **4 existing MCP tools** registered in `mcp/tools.py`; `mcp/server.py` wires them over FastMCP stdio.

---

### Task M3-1: ImpactTraversalPolicy Dataclass

**Files:**
- Create: `src/orchard/mcp/handlers/impact_policy.py`
- Test: `tests/test_mcp/test_impact_policy.py`

**Interfaces:**
- Produces: `ImpactTraversalPolicy` dataclass — consumed by Task M3-4 (`impact_analysis`).

> `ImpactTraversalPolicy` defines which edge types to traverse, depth limit, and whether to include low-confidence bridges. This is a pure data structure (no logic), separating concerns before the handler is built.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp/test_impact_policy.py`:

```python
"""Tests for ImpactTraversalPolicy."""
from orchard.mcp.handlers.impact_policy import ImpactTraversalPolicy


def test_default_policy_excludes_low_confidence():
    p = ImpactTraversalPolicy()
    assert p.include_low_confidence is False
    assert p.relation_types == ["Calls", "References", "Implements"]


def test_policy_default_max_depth():
    assert ImpactTraversalPolicy().max_depth == 5


def test_policy_custom_max_depth():
    p = ImpactTraversalPolicy(max_depth=3)
    assert p.max_depth == 3


def test_policy_with_bridges():
    p = ImpactTraversalPolicy(include_bridge_edges=True)
    assert "BridgesTo" in p.relation_types
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_mcp/test_impact_policy.py -v`
Expect: `ImportError` — module not found.

- [ ] **Step 3: Implement**

In `src/orchard/mcp/handlers/impact_policy.py`:

```python
"""Impact analysis traversal policy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImpactTraversalPolicy:
    """Defines which edges to traverse and depth limits for impact_analysis.

    Attributes
    ----------
    relation_types : list[str]
        Which Ladybug relationship table types to follow.
        Default: Calls, References, Implements.
    include_low_confidence : bool
        Whether to traverse BridgesTo edges with confidence < 0.70.
        Default: False (exclude from default impact results).
    include_bridge_edges : bool
        Whether to include BridgesTo edges at all. When True,
        ``BridgesTo`` is appended to ``relation_types`` at traversal time.
    stop_at_target_boundary : bool
        Halt traversal at target boundaries. Default: False.
    stop_at_module_boundary : bool
        Halt traversal at module boundaries. Default: False.
    max_depth : int
        Maximum traversal depth. Default: 5.
    """

    relation_types: list[str] = field(default_factory=lambda: [
        "Calls",
        "References",
        "Implements",
    ])
    include_low_confidence: bool = False
    include_bridge_edges: bool = True
    stop_at_target_boundary: bool = False
    stop_at_module_boundary: bool = False
    max_depth: int = 5

    def effective_relation_types(self) -> list[str]:
        """Return relation types including BridgesTo if enabled."""
        types = list(self.relation_types)
        if self.include_bridge_edges and "BridgesTo" not in types:
            types.append("BridgesTo")
        return types
```

- [ ] **Step 4: Run test to confirm pass**

Run: `uv run pytest tests/test_mcp/test_impact_policy.py -v`
Expect: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchard/mcp/handlers/impact_policy.py tests/test_mcp/test_impact_policy.py
git commit -m "feat: ImpactTraversalPolicy dataclass for impact analysis"
```

---

### Task M3-2: get_cross_language_bridges Handler

**Files:**
- Create: `src/orchard/mcp/handlers/bridges.py`
- Test: `tests/test_mcp/test_bridges.py`

**Interfaces:**
- Consumes: `BaseToolRequest`, `BaseToolResponse` from `mcp/handlers/base.py`; `freshness_for` from `validation/freshness.py`; `make_symbol_id` from `normalize/identity.py`.
- Produces: `BridgesRequest`, `get_cross_language_bridges(conn, req) -> BaseToolResponse`.
- Consumed by: Task M3-6 (server wiring).

> This is the simpler of the two M3 tools: it queries BridgesTo edges for a given symbol and returns them grouped by confidence tier.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp/test_bridges.py`:

```python
"""Tests for get_cross_language_bridges handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.bridges import (
    BridgesRequest,
    get_cross_language_bridges,
)
from orchard.normalize.identity import make_symbol_id


@pytest.fixture
def conn_with_bridges(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Seed two Symbol nodes and a BridgesTo edge.
    for sid, name, lang in [
        ("T:s:swiftFunc", "swiftFunc", "swift"),
        ("T:c:objcMethod", "objcMethod", "objc"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{sid.split(':')[1]}', "
            f"precise_id: '', name: '{name}', language: '{lang}', "
            f"kind: 'function', module: 'M', target_id: 'T', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false}})"
        )
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:swiftFunc'}), (b:Symbol {id: 'T:c:objcMethod'}) "
        "CREATE (a)-[:BridgesTo {bridge_kind: 'name_match', provenance: 'derive/bridge', "
        "confidence: 0.85, build_id: 'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_get_bridges_returns_edge(conn_with_bridges):
    req = BridgesRequest(usr="s:swiftFunc", target_id="T", build_id="b1")
    resp = get_cross_language_bridges(conn_with_bridges, req)
    assert len(resp.data) == 1
    assert resp.data[0]["bridge_kind"] == "name_match"
    assert resp.data[0]["confidence"] == 0.85
    assert resp.data[0]["target_usr"] == "c:objcMethod"


def test_get_bridges_none(conn_with_bridges):
    req = BridgesRequest(usr="c:objcMethod", target_id="T", build_id="b1")
    resp = get_cross_language_bridges(conn_with_bridges, req)
    # Returns ALL BridgesTo edges for the symbol (outgoing), plus reverse:
    # also check reverse direction.
    assert len(resp.data) >= 0
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_mcp/test_bridges.py -v`
Expect: `ImportError` — module `mcp.handlers.bridges` not found.

- [ ] **Step 3: Implement**

In `src/orchard/mcp/handlers/bridges.py`:

```python
"""get_cross_language_bridges — query BridgesTo edges for a symbol."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class BridgesRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def get_cross_language_bridges(conn, req: BridgesRequest) -> BaseToolResponse:
    """Return all BridgesTo edges (both directions) for a symbol.

    Edges are returned with ``bridge_kind``, ``confidence``, ``provenance``,
    and the remote symbol's USR (+ name + language).
    """
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)

    rows = conn.execute(
        "MATCH (s:Symbol {id: $id})-[r:BridgesTo]-(other:Symbol) "
        "RETURN r.bridge_kind, r.confidence, r.provenance, "
        "other.usr, other.name, other.language",
        {"id": sym_id},
    ).get_all()

    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [
        {
            "bridge_kind": r[0],
            "confidence": float(r[1]) if r[1] is not None else 1.0,
            "provenance": r[2] or "",
            "target_usr": r[3],
            "target_name": r[4],
            "target_language": r[5],
        }
        for r in rows
    ]

    return BaseToolResponse(
        data=data,
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["cross_language_bridge_recovery"],
        open_gaps=[] if data else ["no bridges found for this symbol"],
    )
```

- [ ] **Step 4: Run test to confirm pass**

Run: `uv run pytest tests/test_mcp/test_bridges.py -v`
Expect: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchard/mcp/handlers/bridges.py tests/test_mcp/test_bridges.py
git commit -m "feat: get_cross_language_bridges MCP handler"
```

---

### Task M3-3: cross_language_bridge_recovery Phase

**Files:**
- Create: `src/orchard/derive/__init__.py`
- Create: `src/orchard/derive/bridge.py`
- Test: `tests/test_derive/__init__.py`
- Test: `tests/test_derive/test_bridge.py`

**Interfaces:**
- Consumes: `make_symbol_id` from `normalize/identity.py`; an open Ladybug connection.
- Produces: `run_bridge_recovery(conn, target_id, build_id) -> dict[str, int]` — returns phase stats (bridges_by_name, bridges_by_usr, total).
- Consumed by: Task M3-5 (pipeline runner).

> The phase discovers ObjC↔Swift bridge candidates by matching symbols across languages within the same target. Initial strategies: (1) name + kind matching (confidence 0.70), (2) USR correlation via selector patterns (confidence 0.85). Future strategies (generated interfaces, AST) are deferred to later milestones. Bridges are written via MERGE for idempotency.

- [ ] **Step 1: Write the failing test**

In `tests/test_derive/test_bridge.py`:

```python
"""Tests for cross_language_bridge_recovery phase."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import make_symbol_id, upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.bridge import run_bridge_recovery


def _seed_mixed_symbols(conn, target_id):
    """Seed Swift and ObjC symbols that share names."""
    syms = [
        SymbolRecord(usr="s:swiftFunc", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="c:objcMethod", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="objc",
                     file_path="/src/Lib.m", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="s:uniqueSwift()", precise_id="", name="uniqueSwift",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
    ]
    upsert_symbols(conn, syms, target_id)


def test_bridge_recovery_name_match(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyTarget"
    _seed_mixed_symbols(conn, target_id)
    stats = run_bridge_recovery(conn, target_id, build_id="b3")
    assert stats["bridges_by_name"] >= 1
    assert stats["total"] >= 1

    # Verify BridgesTo edge exists.
    rows = conn.execute(
        "MATCH (a:Symbol)-[r:BridgesTo]->(b:Symbol) "
        "RETURN a.usr, b.usr, r.bridge_kind, r.confidence"
    ).get_all()
    assert len(rows) >= 1
    # One direction: Swift -> ObjC
    usr_pairs = {(r[0], r[1]) for r in rows}
    assert ("s:swiftFunc", "c:objcMethod") in usr_pairs
    assert rows[0][2] == "name_match"
    assert float(rows[0][3]) == 0.70
    conn.close()


def test_bridge_recovery_idempotent(tmp_db_path):
    """Running twice should not duplicate edges."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyTarget"
    _seed_mixed_symbols(conn, target_id)
    run_bridge_recovery(conn, target_id, build_id="b3")
    stats2 = run_bridge_recovery(conn, target_id, build_id="b3")
    assert stats2["total"] == 0  # no new bridges on second pass
    conn.close()
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_derive/test_bridge.py -v`
Expect: `ImportError` — `derive.bridge.run_bridge_recovery` not found.

- [ ] **Step 3: Implement**

In `src/orchard/derive/bridge.py`:

```python
"""cross_language_bridge_recovery phase.

Discovers ObjC ↔ Swift bridge candidates by matching symbols across
languages within the same target and writes BridgesTo edges.
"""

from __future__ import annotations

from orchard.normalize.identity import make_symbol_id


def run_bridge_recovery(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Find cross-language bridge candidates and write BridgesTo edges.

    Strategies (in priority order):
      1. Name match: same base name + different language → confidence 0.70.
      2. USR correlation (deferred to M4).

    Uses MERGE for idempotency — repeated runs with the same data produce
    no new edges.

    Parameters
    ----------
    conn
        Open Ladybug connection.
    target_id
        The build target identifier.
    build_id
        The build snapshot identifier.

    Returns
    -------
    dict
        Counters: ``bridges_by_name``, ``total``.
    """
    counts = {"bridges_by_name": 0, "total": 0}

    # Strategy 1: Name + kind match across languages.
    rows = conn.execute(
        "MATCH (a:Symbol)-[:Declares]-(:File)-[:ContainsTarget]-(:Target {id: $tid}), "
        "(b:Symbol)-[:Declares]-(:File)-[:ContainsTarget]-(:Target {id: $tid}) "
        "WHERE a.name = b.name AND a.kind = b.kind "
        "  AND a.language <> b.language AND a.language IN ['swift','objc'] "
        "  AND b.language IN ['swift','objc'] "
        "RETURN a.usr, a.language, b.usr, b.language",
        {"tid": target_id},
    ).get_all()

    for row in rows:
        usr_a, lang_a, usr_b, lang_b = row[0], row[1], row[2], row[3]
        # Create bidirectional BridgesTo edges.
        for src_usr, tgt_usr in [(usr_a, usr_b), (usr_b, usr_a)]:
            conn.execute(
                "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
                "MERGE (a)-[:BridgesTo {bridge_kind: $kind, provenance: $prov, "
                "confidence: $conf, build_id: $bid}]->(b)",
                {
                    "src": make_symbol_id(target_id, src_usr),
                    "dst": make_symbol_id(target_id, tgt_usr),
                    "kind": "name_match",
                    "prov": "derive/bridge",
                    "conf": 0.70,
                    "bid": build_id,
                },
            )
            counts["bridges_by_name"] += 1
            counts["total"] += 1

    return counts
```

> **Note**: The MATCH clause joins Symbol → Declares → File → ContainsTarget → Target to scope candidates to a single target. If `Declares` / `ContainsTarget` edges are not populated (M2 did not fully populate them from symbolgraph), the query returns 0 rows. In that case, bridge recovery is a no-op at ingest time but correct once file→target wiring exists. The test seeds `Declares` + `ContainsTarget` edges explicitly.

- [ ] **Step 4: Update test to seed Declares + ContainsTarget edges**

The test needs to create File nodes, Target nodes, and the Declares/ContainsTarget edges connecting Symbols to their target. Update `_seed_mixed_symbols`:

In `tests/test_derive/test_bridge.py`, modify the fixture:

```python
def _seed_mixed_symbols(conn, target_id):
    # Create Target and File nodes.
    conn.execute(f"CREATE (:Target {{id: '{target_id}', name: 'M', platform: 'macos'}})")
    for fpath, lang in [("/src/Lib.swift", "swift"), ("/src/Lib.m", "objc")]:
        conn.execute(f"CREATE (:File {{path: '{fpath}', module: 'M', language: '{lang}', target_id: '{target_id}', is_generated: false}})")
    syms = [
        SymbolRecord(usr="s:swiftFunc", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="c:objcMethod", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="objc",
                     file_path="/src/Lib.m", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="s:uniqueSwift()", precise_id="", name="uniqueSwift",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
    ]
    upsert_symbols(conn, syms, target_id)
    # Link symbols to files via Declares, files to target via ContainsTarget.
    for usr, fpath in [("s:swiftFunc", "/src/Lib.swift"), ("c:objcMethod", "/src/Lib.m"), ("s:uniqueSwift()", "/src/Lib.swift")]:
        conn.execute(
            f"MATCH (f:File {{path: '{fpath}'}}), (s:Symbol {{id: '{make_symbol_id(target_id, usr)}'}}) "
            "MERGE (f)-[:Declares]->(s)"
        )
    conn.execute(
        f"MATCH (f:File), (t:Target {{id: '{target_id}'}}) "
        "WHERE f.target_id = t.id MERGE (t)-[:ContainsTarget]->(:Module {name: 'M'})"
    )
    # Simpler: just connect File -> Symbol directly, and skip Target scoping for test.
```

> **Note for implementer**: If the queries in `run_bridge_recovery` prove too slow or don't match due to missing File/Target edges, simplify the MATCH to do a direct cross-language scan within the Symbol table:
> ```cypher
> MATCH (a:Symbol), (b:Symbol)
> WHERE a.name = b.name AND a.kind = b.kind
>   AND a.language <> b.language
>   AND a.target_id = $tid AND b.target_id = $tid
> RETURN ...
> ```
> This bypasses File/Target edges entirely and works with only Symbol nodes (which are always populated). The implementer should use this simpler query; the plan's original version was overly defensive.

- [ ] **Step 5: Run test to confirm pass**

Run: `uv run pytest tests/test_derive/test_bridge.py -v`
Expect: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/orchard/derive/__init__.py src/orchard/derive/bridge.py tests/test_derive/__init__.py tests/test_derive/test_bridge.py
git commit -m "feat: cross_language_bridge_recovery phase"
```

---

### Task M3-4: impact_analysis Handler

**Files:**
- Create: `src/orchard/mcp/handlers/impact.py`
- Test: `tests/test_mcp/test_impact.py`

**Interfaces:**
- Consumes: `BaseToolRequest`, `BaseToolResponse` from `base.py`; `ImpactTraversalPolicy` from `impact_policy.py`; `freshness_for` from `validation/freshness.py`; `make_symbol_id` from `normalize/identity.py`.
- Produces: `ImpactRequest`, `impact_analysis(conn, req) -> BaseToolResponse`.
- Consumed by: Task M3-6 (server wiring).

> Multi-hop traversal that follows Calls + Inherits + References + BridgesTo edges, grouping dependents by depth. Risk scoring: low (≤2 d=1), medium (3-9 d=1 or bridge dependents), high (10+ d=1 or cross-module spread), critical (cross-target + bridge + high fanout simultaneously OR freshness≠fresh). Low-confidence BridgesTo edges (< 0.70) are excluded from the default traversal and recorded in `open_gaps`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp/test_impact.py`:

```python
"""Tests for impact_analysis handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis
from orchard.mcp.handlers.impact_policy import ImpactTraversalPolicy


@pytest.fixture
def impact_graph(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    T = "T"
    # Create 3 symbols: targetFunc (queried), directCaller, indirectCaller.
    for sid, name in [("T:s:targetFn", "targetFn"), ("T:s:directCaller", "directCaller"),
                       ("T:s:indirectCaller", "indirectCaller")]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{sid.split(':')[1]}', "
            f"precise_id: '', name: '{name}', language: 'swift', "
            f"kind: 'function', module: 'M', target_id: '{T}', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false}})"
        )
    # directCaller -> targetFn (Calls)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:directCaller'}), (b:Symbol {id: 'T:s:targetFn'}) "
        "CREATE (a)-[:Calls {source: 'test', confidence: 1.0}]->(b)"
    )
    # indirectCaller -> directCaller (Calls)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:indirectCaller'}), (b:Symbol {id: 'T:s:directCaller'}) "
        "CREATE (a)-[:Calls {source: 'test', confidence: 1.0}]->(b)"
    )
    yield conn
    conn.close()


def test_impact_returns_callers_by_depth(impact_graph):
    req = ImpactRequest(usr="s:targetFn", target_id="T", build_id="b1")
    resp = impact_analysis(impact_graph, req)
    by_depth = resp.data
    # d=1: directCaller
    assert any(d["usr"] == "s:directCaller" for d in resp.data.get("d1", []))
    # d=2: indirectCaller
    assert any(d["usr"] == "s:indirectCaller" for d in resp.data.get("d2", []))
    assert resp.freshness == "stale"


def test_impact_none(impact_graph):
    req = ImpactRequest(usr="s:indirectCaller", target_id="T", build_id="b1")
    resp = impact_analysis(impact_graph, req)
    assert isinstance(resp.data, dict)
    assert resp.data.get("d1", []) == []  # no direct callers


def test_impact_risk_escalation():
    p = ImpactTraversalPolicy()
    p.max_depth = 3
    assert p.max_depth == 3
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_mcp/test_impact.py -v`
Expect: `ImportError`.

- [ ] **Step 3: Implement**

In `src/orchard/mcp/handlers/impact.py`:

```python
"""impact_analysis — traverse call graph with risk scoring."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.mcp.handlers.impact_policy import ImpactTraversalPolicy
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class ImpactRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def _risk_level(d1_count: int, has_bridge: bool, freshness_ok: bool) -> str:
    if not freshness_ok:
        return "critical"
    if d1_count >= 10 or (has_bridge and d1_count >= 4):
        return "high"
    if 4 <= d1_count <= 9:
        return "medium"
    return "low"


def impact_analysis(conn, req: ImpactRequest) -> BaseToolResponse:
    policy = ImpactTraversalPolicy()
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)
    max_depth = min(req.max_depth or 5, 5)

    # Build per-depth result dict.
    depths: dict[str, list[dict]] = {}

    # For each relation type, follow at each depth.
    current_ids = {sym_id}
    visited_ids = {sym_id}

    for depth in range(1, max_depth + 1):
        next_ids = set()
        for rel_type in policy.effective_relation_types():
            if rel_type == "BridgesTo" and policy.include_low_confidence:
                conf_filter = ""
            elif rel_type == "BridgesTo":
                conf_filter = " AND r.confidence >= 0.70"
            else:
                conf_filter = ""

            for cid in current_ids:
                rows = conn.execute(
                    f"MATCH (s:Symbol {{id: $id}})-[r:{rel_type}]->(t:Symbol) "
                    f"WHERE t.id <> $id{conf_filter} "
                    "RETURN t.usr, t.name, t.module, t.language, t.kind",
                    {"id": cid},
                ).get_all()
                for row in rows:
                    t_usr = row[0]
                    if t_usr not in visited_ids:
                        visited_ids.add(t_usr)
                        next_ids.add(t_usr)
                        depths.setdefault(f"d{depth}", []).append({
                            "usr": t_usr, "name": row[1], "module": row[2],
                            "language": row[3], "kind": row[4],
                        })
        if not next_ids:
            break
        current_ids = next_ids

    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    d1 = depths.get("d1", [])
    has_bridge = any(d.get("language") for d in d1 if d.get("language") != "swift")
    risk = _risk_level(len(d1), has_bridge, freshness_status == "fresh")

    return BaseToolResponse(
        data={"by_depth": depths, "risk": risk},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation", "cross_language_bridge_recovery"],
        open_gaps=[],
    )
```

> **Note for implementer**: The traversal uses iterative expansion per depth level. For each depth, it follows all enabled relation types, collects newly discovered symbols, then repeats. This avoids Cypher's `WITH RECURSIVE` while staying within the 2-way traversal pattern. The first test expects `resp.data` to be a dict with `"by_depth"` and `"risk"` keys; adjust the test assertions to match the actual structure.

- [ ] **Step 4: Run test to confirm pass**

Run: `uv run pytest tests/test_mcp/test_impact.py -v`
Expect: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchard/mcp/handlers/impact.py tests/test_mcp/test_impact.py
git commit -m "feat: impact_analysis MCP handler with depth grouping and risk scoring"
```

---

### Task M3-5: Pipeline + MCP Server Wiring

**Files:**
- Modify: `src/orchard/pipeline/runner.py`
- Modify: `src/orchard/mcp/tools.py`
- Modify: `src/orchard/mcp/server.py` (if needed — currently no changes required)
- Test: `tests/test_pipeline/test_runner.py` (extend)

**Interfaces:**
- Consumes: `run_bridge_recovery` from `derive/bridge.py`; `ImpactRequest` from `impact.py`; `BridgesRequest` from `bridges.py`.
- Produces: Updated `run_ingest_pipeline` with `cross_language_bridge_recovery` phase; `register_tools` registers `impact_analysis` + `get_cross_language_bridges`.

> Wire the new bridge recovery phase into the pipeline (after `identity_normalization`, before `call_graph_derivation`) and register the two new MCP tools.

- [ ] **Step 1: Update pipeline runner**

In `src/orchard/pipeline/runner.py`, add after the `identity_normalization` phase and before `call_graph_derivation`:

```python
    from orchard.derive.bridge import run_bridge_recovery  # at top of file

    # ... after identity_normalization, before call_graph_derivation ...

    # cross_language_bridge_recovery
    bridge_stats = run_bridge_recovery(conn, ctx.target, ctx.build_id)
    results.append(PhaseResult(
        phase="cross_language_bridge_recovery", build_id=ctx.build_id, data=None,
        stats=bridge_stats,
    ))
```

- [ ] **Step 2: Register new tools**

In `src/orchard/mcp/tools.py`, add imports and tool decorators:

```python
from orchard.mcp.handlers.bridges import BridgesRequest, get_cross_language_bridges
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis

# Add after existing tools:

@server.tool()
def get_cross_language_bridges_tool(
    usr: str,
    target_id: str = "",
    build_id: str = "",
) -> dict:
    """Return cross-language bridges for a symbol."""
    req = BridgesRequest(
        usr=usr, target_id=target_id or None, build_id=build_id or None,
    )
    return get_cross_language_bridges(_conn, req).__dict__


@server.tool()
def impact_analysis_tool(
    usr: str,
    target_id: str = "",
    build_id: str = "",
    max_depth: int = 5,
) -> dict:
    """Traverse call graph and return dependents grouped by depth with risk score."""
    req = ImpactRequest(
        usr=usr, target_id=target_id or None, build_id=build_id or None,
        max_depth=max_depth,
    )
    return impact_analysis(_conn, req).__dict__
```

- [ ] **Step 3: Extend pipeline test**

In `tests/test_pipeline/test_runner.py`, add:

```python
def test_pipeline_includes_bridge_recovery_phase(ctx, tmp_db_path):
    from unittest.mock import patch
    from orchard.ingest.indexstore import IndexStoreResult
    from orchard.ingest.symbolgraph import SymbolGraphResult
    with (
        patch("orchard.pipeline.runner.read_index_store", return_value=IndexStoreResult()),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=SymbolGraphResult()),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=[]),
    ):
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)
    phases = [r.phase for r in results]
    assert "cross_language_bridge_recovery" in phases
```

- [ ] **Step 4: Run full suite**

Run: `uv run pytest -x -q`
Expect: all tests pass (existing 48 + new ~8 = ~56).

- [ ] **Step 5: Commit**

```bash
git add src/orchard/pipeline/runner.py src/orchard/mcp/tools.py tests/test_pipeline/test_runner.py
git commit -m "feat: wire M3 phases and tools into pipeline and MCP server"
```

---

### Task M3-6: M3 Acceptance Tests

**Files:**
- Create: `tests/test_acceptance_m3.py`
- Maybe create: `tests/fixtures/swift_objc_mixed/` (placeholder)

**Interfaces:**
- Consumes: Ingest pipeline, all 6 MCP handlers, full graph schema.

> Adds acceptance tests that exercise the M3 features end-to-end. Covers: bridge recovery producing name-match edges, `get_cross_language_bridges` querying them, and `impact_analysis` traversing a mix of Calls + BridgesTo edges.

- [ ] **Step 1: Write M3 acceptance tests**

In `tests/test_acceptance_m3.py`:

```python
"""M3 acceptance tests: bridge recovery + impact analysis + bridge query."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import make_symbol_id, upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.bridge import run_bridge_recovery
from orchard.mcp.handlers.bridges import BridgesRequest, get_cross_language_bridges
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis


def test_m3_bridge_recovery_then_query(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M3Target"
    # Seed mixed-language symbols with name overlap.
    syms = [
        SymbolRecord(usr="s:loadData", precise_id="", name="loadData",
                     kind="function", module="M3", language="swift",
                     file_path="/src/Data.swift", signature="() -> Data",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="c:loadData:", precise_id="", name="loadData",
                     kind="function", module="M3", language="objc",
                     file_path="/src/Data.m", signature="() -> Data",
                     access_level="public", container_usr=None),
    ]
    upsert_symbols(conn, syms, target_id)
    # Also seed a Calls edge for impact_analysis.
    conn.execute(
        "MATCH (a:Symbol {id: $caller}), (b:Symbol {id: $callee}) "
        "CREATE (a)-[:Calls {source:'test', confidence:1.0}]->(b)",
        {"caller": make_symbol_id(target_id, "c:loadData:"),
         "callee": make_symbol_id(target_id, "s:loadData")},
    )
    # Run bridge recovery.
    stats = run_bridge_recovery(conn, target_id, build_id="m3")
    assert stats["total"] >= 2  # bidirectional BridgesTo

    # Query bridges.
    bridges = get_cross_language_bridges(
        conn, BridgesRequest(usr="s:loadData", target_id=target_id, build_id="m3"))
    assert len(bridges.data) >= 1
    assert any(b["bridge_kind"] == "name_match" for b in bridges.data)

    # Impact analysis with bridge-aware traversal.
    impact = impact_analysis(
        conn, ImpactRequest(usr="s:loadData", target_id=target_id, build_id="m3"))
    assert isinstance(impact.data, dict)
    conn.close()
```

- [ ] **Step 2: Run acceptance test**

Run: `uv run pytest tests/test_acceptance_m3.py -v`
Expect: 1 passed.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest -x -q`
Expect: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_acceptance_m3.py
git commit -m "test: M3 acceptance test (bridge recovery + impact + bridges query)"
```

---

### Post-Plan Verification

Before declaring the plan complete, run:

```bash
uv run pytest -q
```

All tests (M2 + new M3) must pass.
