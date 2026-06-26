# orchard vs GitNexus on ios-client: First-Pass Comparison

## Scope

- repository: `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client`
- pass: first-pass
- scenario count: 8
- reviewer model: single maintainer
- date: 2026-06-26
- orchard: 234K symbols, 615K Calls, 1.5M Contains, 23K Implements, 7K Inherits (IndexStore source)
- GitNexus: 151K nodes, 103K Calls, 640K edges (AST source)

## 1. Scenario-Level Findings

| Scenario | orchard verdict | GitNexus verdict | Key evidence | Notes |
|----------|-----------------|------------------|--------------|-------|
| S1 (login entry) | **FAIL** | **Partial** | orchard: iOSLogin ObjC classes entirely missing; GitNexus: semantic search failed but direct lookup found ZMAppLoginHelper+ZPPTUIContainer | orchard's IndexStore build didn't include iOSLogin module |
| S2 (meeting join) | **Partial** | **Partial** | Both found all key symbols but neither provided code-level relationships | orchard: 5/5 symbols found but 0 callers returned; GitNexus: rich methods but impact=markdown-only |
| S3 (login impact) | **FAIL** | **Partial** | orchard: symbol not indexed; GitNexus: 1 code caller found (tryAutoLoginWhenAppLaunched) | GitNexus impact is working but incomplete (1 caller for critical callback) |
| S4 (meeting lifecycle) | **SUCCESS** | **Partial** | orchard found cross-language ObjC→C++ callers (CSBConfUI::OnConfStatusChanged) that GitNexus missed | orchard's compiler-level data captures C++ dependencies GitNexus can't see |
| S5 (protocol chain) | **FAIL** | **SUCCESS** | orchard: hierarchy empty despite SymbolGraph ingest; GitNexus: clear IMPLEMENTS LoginOutterService→LoginServiceProtocol (0.92) | GitNexus protocol conformance resolution works; orchard's Swift SymbolGraph data not connecting |
| S6 (bridge identity) | **FAIL** | **FAIL** | Neither tool resolved a Swift↔ObjC bridge pair | orchard: search hints at bridge USR but symbol lookup fails; GitNexus: semantic search irrelevant |
| S7 (startup flow) | **FAIL** | **FAIL** | orchard: ZPAppDelegate found but callers/callees both empty; GitNexus: methods listed but no flow connections | Neither tool can trace a process from entry to destination |
| S8 (notification recovery) | **Partial** | **FAIL** | orchard: found notification handler + 4 internal callers in ZMNotiManager but recovery path incomplete; GitNexus: no flow at all | orchard gets 1 hop further but neither traces the cross-subsystem handoff |

### Score Summary

| Scenario | orchard | GitNexus |
|----------|---------|----------|
| S1 | 0+0+0+1 = **1** | 1+1+1+1 = **4** |
| S2 | 1+1+1+1 = **4** | 1+1+1+1 = **4** |
| S3 | 0+0+0+2 = **2** | 1+2+2+1 = **6** |
| S4 | 2+2+2+1 = **7** | 1+1+2+1 = **5** |
| S5 | 0+0+0+1 = **1** | 2+2+2+1 = **7** |
| S6 | 0+0+0+2 = **2** | 0+0+0+2 = **2** |
| S7 | 0+0+0+2 = **2** | 0+1+1+2 = **4** |
| S8 | 1+1+1+1 = **4** | 0+1+1+2 = **4** |
| **Total** | **23** | **36** |

> Scores are raw sums of 4 dimensions (task_completion + semantic_correctness + explainability + interaction_cost), each 0-2. Max per scenario = 8, total max = 64.

## 2. Capability Gap Model

### Strongest orchard scenarios
- **S4 (meeting lifecycle impact)**: The only scenario where orchard outperformed GitNexus. Cross-language bridge recovery (ObjC→C++) found C++ callers that GitNexus's AST-based analysis missed. This is the IndexStore compiler-level advantage in action.

### Weakest orchard scenarios
- **S1/S3 (login module)**: The entire iOSLogin module's ObjC classes are missing from the graph. This isn't an edge case — it's a systematic coverage gap. The IndexStore build configuration must not have compiled this module.
- **S5 (protocol chain)**: Swift SymbolGraph data was ingested but produced no hierarchy edges. Protocol conformance is a core Apple semantic feature that orchard currently can't resolve.
- **S7 (flow understanding)**: Entry points exist in the graph but have zero call relationships. The call graph is disconnected for critical paths.

### Repeated failure tags

| Tag | orchard count | GitNexus count | Notes |
|-----|---------------|----------------|-------|
| `call_graph_precision` | 5 (S1,S2,S3,S4,S7) | 2 (S1,S2) | orchard: callers frequently empty; GitNexus: impact=markdown-only for many symbols |
| `index_coverage` | 2 (S1,S3) | 0 | orchard: iOSLogin module systematically missing |
| `process_extraction` | 2 (S7,S8) | 3 (S7,S8) | Neither tool can trace multi-hop execution flows |
| `retrieval_ranking` | 0 | 4 (S1,S6,S7,S8) | GitNexus semantic search consistently fails for natural-language queries |
| `cross_language_bridge` | 2 (S4,S6) | 2 (S4,S6) | orchard: bridge data in graph but unresolvable; GitNexus: ObjC→C++ edges missing |
| `apple_specific_semantics` | 1 (S5) | 0 | orchard: protocol conformance hierarchy empty |
| `symbol_identity` | 3 (S1,S5,S6) | 0 | orchard: symbols indexed but identity not connecting |

### Capability-level interpretation

**orchard's core strength — compiler-level cross-language coverage**: When the IndexStore covers a module, orchard captures relationships (especially ObjC↔C++) that GitNexus's AST analysis misses entirely. S4 was the decisive proof point.

**orchard's core weakness — disconnected graph**: Despite 615K CALL edges, the relationships aren't surfacing in queries. `find_callers` returns empty for most symbols. The graph data exists but the query paths aren't resolving. This suggests a query implementation gap rather than a data gap.

**GitNexus's core strength — symbol resolution**: When you know the exact symbol name, GitNexus delivers rich method-level detail with high confidence. Protocol conformance, method listings, and file paths are all solid.

**GitNexus's core weakness — semantic search**: Every natural-language query across all 8 scenarios returned irrelevant results. A maintainer must already know the exact symbol name to get value from GitNexus.

**Shared weakness — process tracing**: Neither tool can trace a multi-hop execution flow. This is the biggest shared gap for "how does X work?" maintainer tasks.

## 3. Orchard Roadmap Guidance

### Directly Borrow

- **GitNexus's symbol context format**: The `context()` output — categorized incoming (IMPORTS, IMPLEMENTS, CALLS) and outgoing (HAS_METHOD) with confidence scores — is more consumable than orchard's raw USR-based output. orchard should adopt a similar structured representation.
- **GitNexus's protocol resolution**: The bidirectional IMPLEMENTS relationship lookup is clean and reliable. orchard's SymbolGraph data should produce the same given proper ingest.

### Apple-Specific Rebuild

- **IndexStore module coverage (MUST FIX NOW)**: The iOSLogin module gap is unacceptable. orchard must verify that all modules in the Xcode workspace are compiled into the ingested IndexStore. This is the #1 priority — without it, orchard can't be trusted for impact analysis on arbitrary symbols.
- **Call graph query resolution**: 615K CALL edges exist but `find_callers` returns empty. This suggests the query engine isn't traversing the edges correctly. Debug with `ZPJoinConfHelper` (known symbol, known calls) to fix the traversal.
- **Protocol conformance chains**: SymbolGraph data is ingested (evidenced by `swift_symbolgraph_ingest` in S5) but produces empty hierarchies. The data pipeline is connected but the query layer isn't reading it.
- **Cross-language bridge identity (S6)**: SMServiceManager.login has both ObjC USR format (`c:@CM@...objc(cs)SMServiceManager(cm)login`) and Swift language label. The data is in the graph but the `symbol` command can't resolve it. This is a query bug, not a data gap.

### Explicitly Not Chased

- **Natural-language semantic search**: GitNexus's BM25+vector approach consistently failed on this codebase. This capability isn't worth replicating until proven on Apple-scale repos.
- **Pre-computed execution flows (Process)**: Both tools failed at flow tracing. Pre-computed 300-process catalog (GitNexus) didn't help S7/S8. This is a hard problem that shouldn't block near-term orchard improvements.
- **Non-Apple language support**: Not relevant to orchard's mission. GitNexus owns this.

## 4. Priority Order

1. **MUST FIX**: iOSLogin module coverage (S1,S3 — blocks task completion on login/auth scenarios)
2. **MUST FIX**: Call graph traversal bug (S2,S7 — 615K edges exist but aren't queryable)
3. **DIFFERENTIATE NEXT**: Protocol conformance from SymbolGraph (S5 — core Apple semantic)
4. **DIFFERENTIATE NEXT**: Cross-language bridge query resolution (S6 — data exists, queries don't work)
5. **OBSERVE**: Process/flow tracing (S7,S8 — hard problem, both tools fail)
