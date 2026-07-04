# Orchard Indexd Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a long-running local `orchard-indexd` daemon that keeps `IndexStoreDB` alive across ingests so repeated ingests stop paying the `IndexStoreDB open` cost on every run.

**Architecture:** Split the current one-shot `orchard-indexstore-reader` into reusable Swift components, then add a second executable target `orchard-indexd` that exposes a minimal Unix-socket JSON protocol (`warm`, `poll`, `list_files`, `scan_all`). Python ingest keeps owning graph writes and falls back to the existing CLI path if the daemon is unavailable.

**Tech Stack:** Swift 5.9, IndexStoreDB, Unix domain sockets, Python 3, existing Orchard CLI/ingest pipeline, pytest/manual benchmark commands.

---

### Task 1: Extract Reusable Swift Scanner Primitives

**Files:**
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/IndexSession.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/FileDiscovery.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/Scanner.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/StatusPayload.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/main.swift`
- Test: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/ExplicitOutputUnitsTests.swift`

- [ ] **Step 1: Add a failing scanner smoke test that locks current output counts**

```swift
func testReaderScannerHelpersProduceExpectedFixtureRelations() throws {
  let fixture = try buildMinimalSwiftIndex(tmp: tempDir())
  let db = try openIndexStoreDB(storePath: fixture.storePath.path)
  var lines: [String] = []

  let summary = try scanCanonicalSymbolsAndRelations(
    db: db,
    filePaths: [fixture.sourceFile.path],
    emitOccurrences: false,
    emit: { lines.append($0) }
  )

  XCTAssertEqual(summary.symbolCount, 2)
  XCTAssertEqual(summary.relationCount, 1)
  XCTAssertTrue(lines.contains { $0.contains("\"kind\":\"symbol\"") })
  XCTAssertTrue(lines.contains { $0.contains("\"kind\":\"relation\"") })
}
```

- [ ] **Step 2: Run the Swift package tests to verify the new helper symbol is missing**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
swift test --filter ReaderScannerHelpersProduceExpectedFixtureRelations
```

Expected: FAIL with unresolved references such as `scanCanonicalSymbolsAndRelations`.

- [ ] **Step 3: Create `IndexSession.swift` with a reusable `openIndexSession` helper**

```swift
import Foundation
import IndexStoreDB

struct OpenedIndexSession {
  let dbPath: String
  let library: IndexStoreLibrary
  let db: IndexStoreDB
}

func openIndexSession(
  storePath: String,
  dylibPath: String,
  waitUntilDoneInitializing: Bool,
  listenToUnitEvents: Bool
) throws -> OpenedIndexSession {
  let dbPath = persistentDatabasePath(storePath: storePath)
  try FileManager.default.createDirectory(
    atPath: dbPath,
    withIntermediateDirectories: true
  )

  let library = try IndexStoreLibrary(dylibPath: dylibPath)
  let db = try IndexStoreDB(
    storePath: storePath,
    databasePath: dbPath,
    library: library,
    waitUntilDoneInitializing: waitUntilDoneInitializing,
    listenToUnitEvents: listenToUnitEvents
  )

  return OpenedIndexSession(dbPath: dbPath, library: library, db: db)
}
```

- [ ] **Step 4: Create `FileDiscovery.swift` with source-root resolution and changed-file filtering helpers**

```swift
import Foundation
import IndexStoreDB

func enumerateSourceFiles(sourceRoots: [String], underRoot: (String) -> Bool) -> [String] {
  let fm = FileManager.default
  let sourceExtensions: Set<String> = [
    "swift", "m", "mm", "c", "cc", "cpp", "cxx", "c++",
    "h", "hh", "hpp", "hxx",
  ]

  var filePaths: [String] = []
  var seen = Set<String>()
  for root in sourceRoots {
    let baseURL = URL(fileURLWithPath: root)
    if let enumerator = fm.enumerator(at: baseURL, includingPropertiesForKeys: nil) {
      while let url = enumerator.nextObject() as? URL {
        if sourceExtensions.contains(url.pathExtension) {
          let path = url.path
          if underRoot(path) && seen.insert(path).inserted {
            filePaths.append(path)
          }
        }
      }
    }
  }
  return filePaths
}

func filterChangedFiles(
  db: IndexStoreDB,
  allFiles: [String],
  since: Double
) -> (changedFiles: [String], allFiles: [String]) {
  let sinceDate = Date(timeIntervalSince1970: since)
  let changed = allFiles.filter { filePath in
    guard let unitDate = db.dateOfLatestUnitFor(filePath: filePath) else {
      return true
    }
    return unitDate > sinceDate
  }
  return (changed, allFiles)
}
```

- [ ] **Step 5: Create `Scanner.swift` with the extracted pass-1 scan implementation**

```swift
import Foundation
import IndexStoreDB

struct ScanSummary {
  let symbolCount: Int
  let relationCount: Int
  let duplicateUpgradeCount: Int
}

func scanCanonicalSymbolsAndRelations(
  db: IndexStoreDB,
  filePaths: [String],
  emitOccurrences: Bool,
  emit: (String) -> Void,
  progress: ((Int, Int) -> Void)? = nil
) throws -> ScanSummary {
  var bestSlot: [String: CanonicalSlot] = [:]
  var emittedRels = Set<RelationDedupKey>()
  var dupCount = 0
  var processedFileCount = 0

  for file in filePaths {
    processedFileCount += 1
    progress?(processedFileCount, filePaths.count)
    for occ in db.symbolOccurrences(inFilePath: file) {
      // Move the current canonical-symbol selection and relation-emission
      // logic here without changing JSON payload shape.
    }
  }

  for (_, slot) in bestSlot {
    emit(
      "{\"kind\":\"symbol\",\"usr\":\(js(slot.usr)),"
      + "\"name\":\(js(slot.name)),\"symbol_kind\":\(js(slot.symbolKind)),"
      + "\"language\":\(js(slot.language)),\"module\":\(js(slot.module)),"
      + "\"file\":\(js(slot.file))}"
    )
  }

  return ScanSummary(
    symbolCount: bestSlot.count,
    relationCount: emittedRels.count,
    duplicateUpgradeCount: dupCount
  )
}
```

- [ ] **Step 6: Update `main.swift` to call the extracted helpers instead of inlining them**

```swift
let opened = try openIndexSession(
  storePath: storePath,
  dylibPath: dylibPath,
  waitUntilDoneInitializing: true,
  listenToUnitEvents: false
)
let db = opened.db
db.pollForUnitChangesAndWait(isInitialScan: true)

let summary = try scanCanonicalSymbolsAndRelations(
  db: db,
  filePaths: filePaths,
  emitOccurrences: emitOccurrences,
  emit: { writeLine($0) },
  progress: { processed, total in
    if processed == 1 || processed % 250 == 0 || processed == total {
      logInlineProgress(
        scanProgressMessage(processed, total),
        finished: processed == total
      )
    }
  }
)
```

- [ ] **Step 7: Run the focused Swift tests and then the full package tests**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
swift test --filter ReaderScannerHelpersProduceExpectedFixtureRelations
swift test
```

Expected: PASS.

- [ ] **Step 8: Commit the extraction-only refactor**

```bash
git add \
  swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/IndexSession.swift \
  swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/FileDiscovery.swift \
  swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/Scanner.swift \
  swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/StatusPayload.swift \
  swift/orchard-indexstore-reader/Sources/orchard-indexstore-reader/main.swift \
  swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/ExplicitOutputUnitsTests.swift
git commit -m "refactor: extract reusable indexstore reader helpers"
```

### Task 2: Add the `orchard-indexd` Swift Daemon Target

**Files:**
- Modify: `swift/orchard-indexstore-reader/Package.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/Protocol.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift`
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`
- Test: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/ExplicitOutputUnitsTests.swift`

- [ ] **Step 1: Add a daemon target and a red test for session reuse**

```swift
func testIndexdSessionManagerReusesSessionForSameStorePath() throws {
  let manager = SessionManager()
  let first = try manager.getOrCreateSession(
    storePath: "/tmp/a/Index.noindex/DataStore",
    sourceRoots: ["/tmp/a/src"],
    targets: ["MyApp"],
    dylibPath: "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"
  )
  let second = try manager.getOrCreateSession(
    storePath: "/tmp/a/Index.noindex/DataStore",
    sourceRoots: ["/tmp/a/src"],
    targets: ["MyApp"],
    dylibPath: "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"
  )

  XCTAssertEqual(first.session.sessionId, second.session.sessionId)
  XCTAssertTrue(second.reused)
}
```

- [ ] **Step 2: Run the targeted test to confirm the daemon types do not exist yet**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
swift test --filter IndexdSessionManagerReusesSessionForSameStorePath
```

Expected: FAIL with missing `SessionManager`.

- [ ] **Step 3: Add the `orchard-indexd` executable target**

```swift
.executableTarget(
  name: "orchard-indexd",
  dependencies: [
    .product(name: "IndexStoreDB", package: "indexstore-db"),
    .product(name: "IndexStore", package: "indexstore-db"),
  ]
),
```

- [ ] **Step 4: Implement the daemon protocol and session manager**

```swift
struct WarmParams: Decodable {
  let storePath: String
  let sourceRoots: [String]
  let targets: [String]
  let dylibPath: String?
}

final class SessionManager {
  private var sessions: [String: IndexdSession] = [:]
  private let lock = NSLock()

  func getOrCreateSession(
    storePath: String,
    sourceRoots: [String],
    targets: [String],
    dylibPath: String?
  ) throws -> (session: IndexdSession, reused: Bool) {
    let key = persistentDatabasePath(storePath: storePath).split(separator: "/").last.map(String.init) ?? storePath
    lock.lock()
    defer { lock.unlock() }
    if let existing = sessions[key] {
      existing.update(sourceRoots: sourceRoots, targets: targets)
      return (existing, true)
    }
    let created = try IndexdSession(
      sessionId: key,
      storePath: storePath,
      sourceRoots: sourceRoots,
      targets: targets,
      dylibPath: dylibPath
    )
    sessions[key] = created
    return (created, false)
  }
}
```

- [ ] **Step 5: Implement daemon request handling for `warm`, `poll`, `list_files`, and `scan_all`**

```swift
switch request.method {
case "warm":
  let params = try decoder.decode(WarmParams.self, from: request.paramsData)
  let result = try manager.getOrCreateSession(
    storePath: params.storePath,
    sourceRoots: params.sourceRoots,
    targets: params.targets,
    dylibPath: params.dylibPath
  )
  return .ok(id: request.id, result: WarmResult(...))
case "poll":
  ...
case "list_files":
  ...
case "scan_all":
  ...
default:
  return .error(id: request.id, code: "unknown_method", message: request.method)
}
```

- [ ] **Step 6: Build the daemon target and run Swift tests**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
swift build -c release --product orchard-indexd
swift test
```

Expected: PASS, and `.build/release/orchard-indexd` exists.

- [ ] **Step 7: Commit the daemon target**

```bash
git add \
  swift/orchard-indexstore-reader/Package.swift \
  swift/orchard-indexstore-reader/Sources/orchard-indexd \
  swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/ExplicitOutputUnitsTests.swift
git commit -m "feat: add orchard indexd daemon target"
```

### Task 3: Add Python Daemon Client With CLI Fallback

**Files:**
- Modify: `src/orchard/ingest/indexstore.py`
- Modify: `src/orchard/cli.py`
- Test: `tests/test_docs/test_orchard_skill_boundary.py`

- [ ] **Step 1: Add a failing Python unit test for daemon-first fallback behavior**

```python
def test_read_index_store_falls_back_to_cli_when_indexd_unavailable(monkeypatch):
    import orchard.ingest.indexstore as mod

    monkeypatch.setattr(mod, "_run_indexd", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(mod, "_run_cli", lambda *args, **kwargs: (["{\"kind\":\"symbol\",\"usr\":\"u\",\"name\":\"f\",\"symbol_kind\":\"function\",\"language\":\"swift\",\"module\":\"M\",\"file\":\"/tmp/F.swift\"}"], "{\"changed\": [], \"all\": []}"))

    result, file_status, _ = mod.read_index_store("/tmp/store", "MyApp")
    assert len(result.symbols) == 1
    assert file_status == {"changed": [], "all": []}
```

- [ ] **Step 2: Run the targeted Python test and confirm `_run_indexd` is missing**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2
pytest tests/test_docs/test_orchard_skill_boundary.py -k indexd -q
```

Expected: FAIL because `_run_indexd` does not exist.

- [ ] **Step 3: Implement `_run_indexd`, `_run_reader`, and fallback behavior**

```python
def _run_indexd(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    list_files: bool = False,
    targets: list[str] | None = None,
    emit_occurrences: bool = False,
    dump_unit_output_paths: bool = False,
):
    client = _IndexdClient()
    warm = client.request("warm", {
        "storePath": index_store_path,
        "sourceRoots": source_roots or ([source_root] if source_root else []),
        "targets": targets or [],
    })
    session_id = warm["sessionId"]
    client.request("poll", {"sessionId": session_id, "isInitialScan": True})
    return client.scan(session_id=session_id, list_files=list_files, dump_unit_output_paths=dump_unit_output_paths)


def _run_reader(...):
    try:
        return _run_indexd(...)
    except Exception:
        return _run_cli(...)
```

- [ ] **Step 4: Update `read_index_store()` to call `_run_reader()` rather than `_run_cli()` directly**

```python
lines, stderr = _run_reader(
    index_store_path,
    source_root=source_root,
    source_roots=source_roots,
    incremental_since=incremental_since,
    targets=targets,
    emit_occurrences=emit_occurrences,
)
```

- [ ] **Step 5: Run the targeted Python test and a representative ingest help command**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2
pytest tests/test_docs/test_orchard_skill_boundary.py -k indexd -q
PYTHONPATH=src python -m orchard.cli ingest --help
```

Expected: PASS test; CLI help still renders.

- [ ] **Step 6: Commit the Python client integration**

```bash
git add src/orchard/ingest/indexstore.py src/orchard/cli.py tests/test_docs/test_orchard_skill_boundary.py
git commit -m "feat: add indexd client fallback for ingest"
```

### Task 4: Benchmark and Validate Reuse on the Example Client Fixture

**Files:**
- Modify: `docs/superpowers/plans/2026-07-02-orchard-indexd.md`

- [ ] **Step 1: Build the release binaries before benchmarking**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
swift build -c release --product orchard-indexstore-reader
swift build -c release --product orchard-indexd
```

Expected: both binaries build successfully.

- [ ] **Step 2: Start the daemon against a temporary local socket**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2/swift/orchard-indexstore-reader
.build/release/orchard-indexd --socket /tmp/orchard-indexd.sock
```

Expected: process stays alive and prints a ready log line.

- [ ] **Step 3: Measure first and second ingest runs on the example fixture**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2
env PYTHONPATH=src ORCHARD_INDEXD_SOCKET=/tmp/orchard-indexd.sock /usr/bin/time -lp python -m orchard.cli ingest \
  --project-dir /path/to/your/xcode-project \
  --index-store /path/to/DerivedData/YourProject-abc/Index.noindex/DataStore \
  --target MyApp \
  --full
env PYTHONPATH=src ORCHARD_INDEXD_SOCKET=/tmp/orchard-indexd.sock /usr/bin/time -lp python -m orchard.cli ingest \
  --project-dir /path/to/your/xcode-project \
  --index-store /path/to/DerivedData/YourProject-abc/Index.noindex/DataStore \
  --target MyApp \
  --full
```

Expected:
- first run may still pay the initial `poll`
- second run should avoid the repeated `IndexStoreDB open 28-36s` cost
- symbol and relation totals should match the existing baseline (`238,039` / `2,378,758`)

- [ ] **Step 4: Verify CLI fallback by running once with an invalid socket**

Run:

```bash
cd /Users/hui.xu/SourceCode/orchard2
env PYTHONPATH=src ORCHARD_INDEXD_SOCKET=/tmp/does-not-exist.sock python -m orchard.cli ingest \
  --project-dir /path/to/your/xcode-project \
  --index-store /path/to/DerivedData/YourProject-abc/Index.noindex/DataStore \
  --target MyApp \
  --incremental
```

Expected: command completes via the legacy CLI path rather than crashing.

- [ ] **Step 5: Record the measured timings and residual risks back into this plan**

```markdown
## Benchmark Notes

- First full ingest with indexd:
  - `read_index_store`: `36.154s`
  - end-to-end `real`: `62.23s`
  - output counts: `238,039 symbols`, `2,378,758 relations`, `7,183 files`
- Second full ingest with indexd:
  - `read_index_store`: `33.729s`
  - end-to-end `real`: `58.62s`
  - output counts: `238,039 symbols`, `2,378,758 relations`, `7,183 files`
- Legacy fallback check:
  - invalid socket path correctly fell back to `orchard-indexstore-reader`
  - fallback `read_index_store` sample run: `67.174s`, same symbol/relation totals
- Residual risks:
  - `pollForUnitChangesAndWait(isInitialScan: true)` still dominates first-run cost
  - daemon protocol currently implements `warm` + `scan`; dedicated `list_files` / `dump_unit_output_paths` methods remain to be added
  - multi-session eviction policy remains out of scope for this phase
```

- [ ] **Step 6: Commit the benchmarked daemon rollout**

```bash
git add docs/superpowers/plans/2026-07-02-orchard-indexd.md
git commit -m "docs: record orchard indexd benchmark results"
```

## Self-Review

- Spec coverage: this plan covers Swift helper extraction, daemon introduction, Python fallback, and benchmark validation against the profiled example fixture.
- Placeholder scan: every task names exact files, concrete commands, and concrete signatures or snippets to implement.
- Type consistency: the plan consistently uses `IndexdSession`, `SessionManager`, `_run_indexd`, `_run_reader`, and `scanCanonicalSymbolsAndRelations`.

Plan complete and saved to `docs/superpowers/plans/2026-07-02-orchard-indexd.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
