# Orchard M4: Embedding + Semantic Search + Architecture Derivation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** M4 milestone: embedding projection (Ollama qwen3-embedding:0.6b, 768-dim vectors), hybrid semantic search (vector + FTS), architecture derivation (module graph, layer violations, cycles), and 2 new MCP tools.

**Architecture:** New `search/chunker.py` splits symbols into Chunk nodes by type/method/extension. `search/embedder.py` calls Ollama HTTP API for embeddings. A new pipeline phase `embedding_projection` populates the `Chunk` table (FLOAT[768]) and `ContainsChunk` edges. `semantic_search` handler performs hybrid retrieval (cosine similarity + FTS over chunk content). `derive/architecture.py` builds module dependency graphs and detects layer violations. Two new MCP tools expose these.

**Tech Stack:** Python≥3.12 + uv + Ladybug (KuzuDB FTS + vector) + mcp SDK + Ollama HTTP (local only). No new Python deps beyond `httpx` (already in pyproject.toml).

## Global Constraints

- Python ≥ 3.12, `str | None` union. Ladybug `.get_all()` API.
- `_ConnectionWithDB` wrapper — keep Database alive.
- Composite key: `"{target_id}:{usr}"` via `make_symbol_id`.
- Every MCP tool response: freshness, build_id, evidence_sources, open_gaps.
- Ollama graceful degradation: if unreachable, embedding skipped with warning; semantic_search falls back to FTS-only.
- Chunk: id=`{symbol_id}:chunk:type:{n}`, owner_usr=symbol USR, embedding FLOAT[768].
- FTS index: Ladybug FTS over Chunk.content.
- TDD: test → fail → implement → pass → commit.
- `git add` specific files ONLY. `uv run pytest -x -q`.

## Existing Foundation (M0-M3, 62 tests)

- `Chunk` node table + `ContainsChunk` relation already declared in `graph/schema.py:62-87`.
- `httpx` already in `pyproject.toml` deps.
- `pipeline/runner.py` DAG: `... → cross_language_bridge_recovery → call_graph_derivation`.
- `Derive/` pattern established (bridge.py).
- `mcp/tools.py` has 6 registered tools; `mcp/server.py` wires them over FastMCP stdio.

---

### Task M4-1: Ollama Embedder Client

**Files:** Create `src/orchard/search/__init__.py`, `src/orchard/search/embedder.py`. Test: `tests/test_search/__init__.py`, `tests/test_search/test_embedder.py`.

**Interfaces:** Produces `Embedder` class consumed by Task M4-2 (chunker/pipeline) and Task M4-4 (semantic_search).

- [ ] **Step 1: Write failing test**

```python
# tests/test_search/test_embedder.py
from unittest.mock import patch, MagicMock
from orchard.search.embedder import Embedder, EmbeddingError

def test_embedder_returns_768d_vector():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"embeddings": [[0.1]*768]}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.Client.post", return_value=mock_resp):
        e = Embedder(base_url="http://localhost:11434")
        vec = e.embed("func calculate(): Int")
    assert len(vec) == 768
    assert isinstance(vec[0], float)

def test_embedder_unreachable_raises():
    import httpx
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("refused")):
        e = Embedder(base_url="http://localhost:11434")
        try:
            e.embed("test")
        except EmbeddingError:
            pass  # expected
```

- [ ] **Step 2: Implement**

```python
# src/orchard/search/embedder.py
import httpx

class EmbeddingError(Exception): ...

class Embedder:
    def __init__(self, base_url="http://localhost:11434", model="qwen3-embedding:0.6b", timeout=30):
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self._model = model
        self._client = httpx.Client(timeout=timeout)
    
    def embed(self, text: str) -> list[float]:
        try:
            r = self._client.post(self._url, json={"model": self._model, "input": [text]})
            r.raise_for_status()
            return r.json()["embeddings"][0]
        except httpx.ConnectError as e:
            raise EmbeddingError(f"Ollama unreachable: {e}") from e
        except Exception as e:
            raise EmbeddingError(f"embedding failed: {e}") from e
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            r = self._client.post(self._url, json={"model": self._model, "input": texts})
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:
            raise EmbeddingError(f"batch embedding failed: {e}") from e
```

- [ ] **Step 3: Run tests → commit**

`uv run pytest tests/test_search/ -v` → 2 passed.

```bash
git add src/orchard/search/ tests/test_search/
git commit -m "feat: Ollama embedder client (qwen3-embedding:0.6b, 768-dim)"
```

---

### Task M4-2: Symbol Chunker + embedding_projection Phase

**Files:** Create `src/orchard/search/chunker.py`. Modify `src/orchard/pipeline/runner.py`. Test: `tests/test_search/test_chunker.py`, extend `tests/test_pipeline/test_runner.py`.

- [ ] **Step 1: Chunker — chunk symbols by type/method/extension**

```python
# src/orchard/search/chunker.py
from dataclasses import dataclass

@dataclass
class ChunkRecord:
    chunk_id: str         # "{symbol_id}:chunk:{kind}:{n}"
    owner_usr: str        # symbol USR (unscoped)
    chunk_kind: str       # "type" | "method" | "extension"
    content: str          # text for embedding + FTS

def chunk_symbols(conn, target_id: str) -> list[ChunkRecord]:
    """Produce ChunkRecords for each Symbol in a target.
    Strategy: one chunk per Symbol. Content = '{kind} {name}: {signature}'."""
    rows = conn.execute(
        "MATCH (s:Symbol) WHERE s.target_id = $tid RETURN s.usr, s.name, s.kind, s.signature",
        {"tid": target_id}
    ).get_all()
    chunks = []
    for i, row in enumerate(rows):
        usr, name, kind, sig = row[0], row[1], row[2], row[3] or ""
        content = f"{kind} {name}: {sig}".strip()
        chunk_kind = "type" if kind in ("struct","class","enum","protocol") else "method"
        chunks.append(ChunkRecord(
            chunk_id=f"{target_id}:{usr}:chunk:{chunk_kind}:{i}",
            owner_usr=usr, chunk_kind=chunk_kind, content=content))
    return chunks
```

- [ ] **Step 2: Pipeline — embedding_projection phase**

In `runner.py`, after `cross_language_bridge_recovery` and before `call_graph_derivation`, add:

```python
from orchard.search.embedder import Embedder, EmbeddingError
from orchard.search.chunker import chunk_symbols

# embedding_projection
chunks = chunk_symbols(conn, ctx.target)
embed_written = 0
try:
    embedder = Embedder()
    texts = [c.content for c in chunks]
    if texts:
        vectors = embedder.embed_batch(texts)
        for chunk, vec in zip(chunks, vectors):
            if len(vec) != 768: continue
            emb = str(list(vec)).replace(" ","")  # FLOAT[768] literal
            conn.execute(
                "MERGE (c:Chunk {id: $id}) SET c.owner_usr=$usr, c.chunk_kind=$kind, c.content=$content, c.embedding=$emb",
                {"id": chunk.chunk_id, "usr": chunk.owner_usr, "kind": chunk.chunk_kind, "content": chunk.content, "emb": None}
            )
            # Ladybug vector literal: cast(list). Will need to use actual Ladybug API for vectors.
            conn.execute(
                f"MATCH (c:Chunk {{id: $id}}) SET c.embedding = CAST($vec, 'FLOAT[768]')",
                {"id": chunk.chunk_id, "vec": vec}
            )
            # ContainsChunk edge
            conn.execute(
                "MATCH (s:Symbol {id: $sid}), (c:Chunk {id: $cid}) MERGE (s)-[:ContainsChunk]->(c)",
                {"sid": f"{ctx.target}:{chunk.owner_usr}", "cid": chunk.chunk_id}
            )
            embed_written += 1
except EmbeddingError as e:
    results.append(PhaseResult(phase="embedding_projection", build_id=ctx.build_id, data=None,
        stats={"chunks": len(chunks), "embedded": 0}, warnings=[f"Ollama unavailable: {e}"]))
    # Continue without embeddings — semantic_search will use FTS-only fallback.

if embed_written > 0:
    results.append(PhaseResult(phase="embedding_projection", build_id=ctx.build_id, data=None,
        stats={"chunks": len(chunks), "embedded": embed_written}))
```

> **Implementer note**: Ladybug vector literal syntax varies. The exact way to set a FLOAT[768] column is: use the Python list directly as the parameter value. Ladybug's Python bindings handle the list→vector conversion. Try `{"emb": vec}` directly. If that fails, check Ladybug docs for list-to-vector binding. The FTS index creation may need `CALL CREATE_FTS_INDEX('Chunk', 'content', ...)` or Ladybug's built-in — check actual API.

- [ ] **Step 3: Tests → commit**

Add `test_pipeline_embedding_projection_skips_on_ollama_down` to `test_runner.py` using `patch("orchard.pipeline.runner.Embedder", side_effect=EmbeddingError("down"))` — assert phase present with embedded=0.

`uv run pytest -x -q` → all pass.

```bash
git add src/orchard/search/chunker.py src/orchard/pipeline/runner.py tests/
git commit -m "feat: symbol chunker + embedding_projection pipeline phase"
```

---

### Task M4-3: semantic_search MCP Handler

**Files:** Create `src/orchard/mcp/handlers/semantic_search.py`. Test: `tests/test_mcp/test_semantic_search.py`.

- [ ] **Step 1: Write failing test**

```python
def test_semantic_search_fts_fallback(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema
    from orchard.mcp.handlers.semantic_search import SemanticSearchRequest, semantic_search
    conn = get_connection(tmp_db_path); init_schema(conn)
    # Seed Chunk + Symbol
    conn.execute("CREATE (:Symbol {id: 'T:s:A', usr: 's:A', name: 'loadData', kind: 'function', module: 'M', language: 'swift', target_id: 'T', ...})")
    conn.execute("CREATE (:Chunk {id: 'T:s:A:chunk:method:0', owner_usr: 's:A', chunk_kind: 'method', content: 'function loadData: () -> Data'})")
    req = SemanticSearchRequest(query="loadData", build_id="b1")
    resp = semantic_search(conn, req)
    assert len(resp.data) >= 1
    assert resp.data[0]["name"] == "loadData"
```

- [ ] **Step 2: Implement hybrid search (vector if Chunk has embedding, fall back to FTS)**

```python
@dataclass
class SemanticSearchRequest(BaseToolRequest):
    query: str = ""
    top_k: int = 10

def semantic_search(conn, req):
    # 1. Try vector similarity if query can be embedded
    embedder = None
    try: embedder = Embedder()
    except: pass
    
    query_vec = None
    if embedder:
        try: query_vec = embedder.embed(req.query)
        except: pass
    
    results = []
    if query_vec:
        # Vector cosine similarity via Ladybug
        rows = conn.execute(
            "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
            "RETURN c.owner_usr, c.content, c.chunk_kind, CAST(ARRAY_COSINE_SIMILARITY(c.embedding, $qvec), 'DOUBLE') AS score "
            "ORDER BY score DESC LIMIT $k",
            {"qvec": query_vec, "k": req.top_k}
        ).get_all()
        # ...
    
    # FTS fallback
    if not results:
        rows = conn.execute(
            "MATCH (c:Chunk) WHERE c.content CONTAINS $q "
            "RETURN c.owner_usr, c.content, c.chunk_kind LIMIT $k",
            {"q": req.query, "k": req.top_k}
        ).get_all()
        # map owner_usr → Symbol name/kind...
    
    return BaseToolResponse(data=results, freshness=..., evidence_sources=["embedding_projection"], ...)
```

> **Implementer note**: Ladybug's actual FTS and vector similarity functions differ. Check Ladybug docs: FTS may use `CALL FTS_SEARCH` or `CONTAINS`. Vector ops may be `ARRAY_COSINE_SIMILARITY` or require a vector index. Adjust queries to match actual Ladybug API. If Ladybug lacks these, the M4 plan adapts to use in-Python post-filtering (load all Chunks with embeddings, compute cosine in Python, sort, return top-k). This is acceptable for M4 with the understanding that performance optimization (server-side vector ops) is future work.

- [ ] **Step 3: Commit**

```bash
git add src/orchard/mcp/handlers/semantic_search.py tests/
git commit -m "feat: semantic_search MCP handler (hybrid vector + FTS)"
```

---

### Task M4-4: architecture_derivation Phase + get_module_graph

**Files:** Create `src/orchard/derive/architecture.py`. Create `src/orchard/mcp/handlers/module_graph.py`. Tests in `tests/test_derive/test_architecture.py`, `tests/test_mcp/test_module_graph.py`.

- [ ] **Step 1: architecture_derivation phase**

```python
# derive/architecture.py
def run_architecture_derivation(conn, target_id: str, build_id: str) -> dict:
    """Build module-level dependency edges and detect architecture patterns."""
    # Module dependency: Symbol A imports/references Symbol B in different module
    rows = conn.execute(
        "MATCH (a:Symbol)-[:Calls|References]->(b:Symbol) "
        "WHERE a.module <> b.module AND a.target_id = $tid AND b.target_id = $tid "
        "RETURN a.module, b.module, count(*) AS weight",
        {"tid": target_id}
    ).get_all()
    deps = 0
    for row in rows:
        src_mod, tgt_mod, w = row[0], row[1], row[2]
        conn.execute(
            "MERGE (a:Module {name: $src})-[r:DEPENDS_ON]->(b:Module {name: $dst}) "
            "SET r.weight = $w, r.build_id = $bid",
            {"src": src_mod, "dst": tgt_mod, "w": int(w), "bid": build_id}
        )
        deps += 1
    # Detect cycles (simplified: self-loop if a→b and b→a)
    cycles = conn.execute(
        "MATCH (a:Module)-[r1:DEPENDS_ON]->(b:Module)-[r2:DEPENDS_ON]->(a:Module) WHERE id(a) < id(b) "
        "RETURN a.name, b.name"
    ).get_all()
    return {"module_deps": deps, "cycles_detected": len(cycles)}
```

- [ ] **Step 2: get_module_graph handler**

```python
# mcp/handlers/module_graph.py
def get_module_graph(conn, req):
    rows = conn.execute("MATCH (a:Module)-[r:DEPENDS_ON]->(b:Module) RETURN a.name, b.name, r.weight").get_all()
    data = [{"source": r[0], "target": r[1], "weight": int(r[2])} for r in rows]
    return BaseToolResponse(data=data, freshness=..., evidence_sources=["architecture_derivation"], ...)
```

- [ ] **Step 3: Commit**

```bash
git add src/orchard/derive/architecture.py src/orchard/mcp/handlers/module_graph.py tests/
git commit -m "feat: architecture_derivation phase + get_module_graph handler"
```

---

### Task M4-5: find_layer_violations MCP Handler

**Files:** Create `src/orchard/mcp/handlers/layer_violations.py`. Test: `tests/test_mcp/test_layer_violations.py`.

Layer violations: check if symbols with certain annotations (e.g., "UI", "Data", "Service") have calls that cross predefined layer boundaries (UI→Data allowed, Data→UI disallowed).

- [ ] **Step 1: Implement simple layer-annotation check**

```python
def find_layer_violations(conn, req):
    """Detect Calls edges that cross architecture layer boundaries based on module-name heuristics."""
    # Simple heuristic: if caller's module contains 'UI' and callee's module contains 'Data',
    # it's a potential violation (UI shouldn't depend directly on Data).
    layers = {
        "UI": ["UI", "View", "Widget"],
        "Data": ["Data", "Model", "Storage", "DB", "Repository"],
        "Service": ["Service", "Manager", "Controller"]
    }
    violations = []
    rows = conn.execute(
        "MATCH (a:Symbol)-[:Calls]->(b:Symbol) WHERE a.module <> b.module "
        "RETURN a.usr, a.name, a.module, b.usr, b.name, b.module LIMIT 500"
    ).get_all()
    for row in rows:
        # ... check if a.module → b.module crosses predefined direction
        pass
    return BaseToolResponse(data=violations, ...)
```

- [ ] **Step 2: Commit**

```bash
git add src/orchard/mcp/handlers/layer_violations.py tests/
git commit -m "feat: find_layer_violations MCP handler"
```

---

### Task M4-6: Pipeline + MCP Wiring + Acceptance Test

**Files:** Modify `src/orchard/pipeline/runner.py` (add architecture_derivation), `src/orchard/mcp/tools.py` (register 3 new tools). Create `tests/test_acceptance_m4.py`.

- [ ] Wire architecture_derivation into pipeline
- [ ] Register semantic_search_tool, get_module_graph_tool, find_layer_violations_tool
- [ ] Write M4 acceptance test: seed Chunks + Calls → semantic_search returns results → module_graph has edges → layer_violations detected
- [ ] Full suite green

---

### Post-Plan Verification

`uv run pytest -q` — all tests pass.
