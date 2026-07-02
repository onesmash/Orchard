# Orchard Indexd Watch-Driven Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session-registered, watch-driven background ingest so `orchard-indexd` can keep the graph current by launching `orchard ingest`, while graph writes remain serialized by a CLI-owned file lock.

**Architecture:** Extend the existing `orchard-indexd` protocol with explicit session registration carrying remembered ingest context, then add daemon-side session scheduling and graph-db-scoped single-flight child execution. Keep all graph mutations inside `orchard ingest`, protected by a non-blocking file lock that returns a dedicated `LOCK_BUSY` exit code for daemon retry logic.

**Tech Stack:** Python (`argparse`, `subprocess`, `fcntl`, `json`), Swift (`Foundation`, `Dispatch`, `Process`, `IndexStoreDB`), pytest, SwiftPM tests

## Global Constraints

- Keep `IndexStoreDB` hot in a long-lived daemon instead of reopening it for every ingest run.
- Detect Xcode / IndexStore changes in the background and trigger graph refresh automatically.
- Preserve one graph-write path by reusing `orchard ingest` rather than teaching the daemon to mutate graph state directly.
- Ensure eventual graph freshness even when a background update collides with a user-started ingest.
- Keep locking semantics shared across all ingest entry points.
- Do not make the daemon write graph database rows directly in this iteration.
- Do not move graph-update business logic out of `orchard ingest`.
- Do not add multi-writer graph concurrency.
- Do not auto-retry every ingest failure; only lock contention gets automatic retry treatment.
- Session identity for v1 is normalized from canonical `index-store` path plus canonical graph database path.
- Target-set differences do not create a new session; they refresh remembered ingest context with last-writer-wins semantics.
- Single-flight scheduling is per graph database path, not global.

---

## File Map

- Modify: `src/orchard/cli.py`
  - Add ingest lock acquisition, daemon-triggered exit semantics, and session registration hook after CLI scope normalization.
- Modify: `src/orchard/ingest/indexstore.py`
  - Extend the Python `orchard-indexd` client with session registration and any required daemon startup arguments.
- Create: `src/orchard/ingest/lock.py`
  - Hold the reusable file-lock implementation, `LOCK_BUSY` exit code constant, and lock-path derivation from graph DB path.
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/Protocol.swift`
  - Define the new session registration payloads and daemon response types.
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift`
  - Route the new RPC, pass CLI path/runtime settings into the daemon, and keep existing methods working.
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift`
  - Replace store-only session identity with `(storePath, graphDBPath)` identity and add graph-db-scoped in-flight coordination.
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`
  - Turn on unit-event listening, track remembered ingest context, generation counters, debounce/retry scheduling, and launch background ingest children.
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IngestContext.swift`
  - Encode normalized remembered ingest arguments plus helper methods for child-process argv generation.
- Create: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift`
  - Add focused Swift tests for session registration, single-flight scope, and lock-busy retry scheduling.
- Modify: `tests/test_ingest/test_indexstore.py`
  - Cover Python client registration, daemon startup argument propagation, and CLI `LOCK_BUSY` helpers.
- Modify: `tests/test_acceptance.py`
  - Cover CLI-visible behavior: registration call from `orchard ingest`, lock-busy exit semantics, and `indexd` management surfaces if they gain new fields.

### Task 1: Add CLI Lock Primitive And Exit Contract

**Files:**
- Create: `src/orchard/ingest/lock.py`
- Modify: `src/orchard/cli.py`
- Modify: `tests/test_acceptance.py`

**Interfaces:**
- Consumes: normalized `ns.db` path from `cmd_ingest(...)`
- Produces: `LOCK_BUSY_EXIT_CODE`, `graph_db_lock_path(graph_db_path: str) -> str`, `try_acquire_graph_db_lock(graph_db_path: str) -> GraphDBLock | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_acceptance.py
from orchard.ingest.lock import LOCK_BUSY_EXIT_CODE, graph_db_lock_path


def test_graph_db_lock_path_hashes_graph_db(tmp_path):
    graph_db = tmp_path / ".orchard" / "graph.db"
    lock_path = graph_db_lock_path(str(graph_db))
    assert lock_path.endswith(".lock")
    assert "orchard-ingest-" in lock_path


def test_cmd_ingest_returns_lock_busy_when_lock_held(monkeypatch, tmp_path):
    from orchard import cli as cli_mod

    graph_db = tmp_path / ".orchard" / "graph.db"
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    graph_db.write_text("", encoding="utf-8")

    class FakeLock:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli_mod, "_conn", lambda _path, read_only=False: (_ for _ in ()).throw(AssertionError("should not open db")))
    monkeypatch.setattr("orchard.ingest.lock.try_acquire_graph_db_lock", lambda _path: None)

    try:
        cli_mod.cmd_ingest([
            "--index-store", "/tmp/IndexStore",
            "--project-dir", str(tmp_path),
            "--target", "Zoom",
            "--db", str(graph_db),
        ])
    except SystemExit as exc:
        assert exc.code == LOCK_BUSY_EXIT_CODE
    else:
        raise AssertionError("expected SystemExit")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_acceptance.py::test_graph_db_lock_path_hashes_graph_db tests/test_acceptance.py::test_cmd_ingest_returns_lock_busy_when_lock_held -v`

Expected: FAIL because `orchard.ingest.lock` does not exist and `cmd_ingest` has no lock-busy exit contract.

- [ ] **Step 3: Write the minimal implementation**

```python
# src/orchard/ingest/lock.py
from __future__ import annotations

import fcntl
import hashlib
from dataclasses import dataclass
from pathlib import Path

LOCK_BUSY_EXIT_CODE = 23


def graph_db_lock_path(graph_db_path: str) -> str:
    canonical = str(Path(graph_db_path).resolve())
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return str(Path.home() / ".orchard" / "locks" / f"orchard-ingest-{digest}.lock")


@dataclass
class GraphDBLock:
    handle: object
    path: str

    def release(self) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()

    def __enter__(self) -> "GraphDBLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


def try_acquire_graph_db_lock(graph_db_path: str) -> GraphDBLock | None:
    lock_path = Path(graph_db_lock_path(graph_db_path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return GraphDBLock(handle=handle, path=str(lock_path))
```

```python
# src/orchard/cli.py (inside cmd_ingest, after ns.db is finalized and before _conn(ns.db))
from orchard.ingest.lock import LOCK_BUSY_EXIT_CODE, try_acquire_graph_db_lock

graph_db_lock = try_acquire_graph_db_lock(ns.db)
if graph_db_lock is None:
    print("INGEST_LOCK_BUSY", file=sys.stderr)
    sys.exit(LOCK_BUSY_EXIT_CODE)
```

```python
# src/orchard/cli.py (wrap the ingest body)
with graph_db_lock:
    conn = _conn(ns.db)
    project_dir = str(Path(ns.project_dir).resolve())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_acceptance.py::test_graph_db_lock_path_hashes_graph_db tests/test_acceptance.py::test_cmd_ingest_returns_lock_busy_when_lock_held -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/ingest/lock.py src/orchard/cli.py tests/test_acceptance.py
git commit -m "feat: add ingest graph db lock contract"
```

### Task 2: Register Or Refresh Sessions From `orchard ingest`

**Files:**
- Modify: `src/orchard/cli.py`
- Modify: `src/orchard/ingest/indexstore.py`
- Modify: `tests/test_ingest/test_indexstore.py`
- Modify: `tests/test_acceptance.py`

**Interfaces:**
- Consumes: normalized `index_store`, `ns.db`, `project_dir`, `targets`, `entry_target`
- Produces: `_IndexdClient.register_session(...) -> dict`, `register_indexd_session(...) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest/test_indexstore.py
def test_indexd_client_register_session_sends_context(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, socket_path):
            self.socket_path = socket_path
        def _request(self, payload):
            captured["payload"] = payload
            return [{"ok": True, "result": {"sessionId": "s1", "registered": True}}]

    monkeypatch.setattr("orchard.ingest.indexstore._IndexdClient._request", FakeClient._request, raising=False)
    client = __import__("orchard.ingest.indexstore", fromlist=["_IndexdClient"])._IndexdClient("/tmp/orchard-indexd.sock")
    result = client.register_session(
        store_path="/tmp/DataStore",
        graph_db_path="/tmp/graph.db",
        ingest_context={
            "projectDir": "/tmp/project",
            "indexStorePath": "/tmp/DataStore",
            "graphDBPath": "/tmp/graph.db",
            "targetArgs": ["Zoom", "zPSApp"],
        },
    )

    assert result["sessionId"] == "s1"
    assert captured["payload"]["method"] == "register_session"
    assert captured["payload"]["params"]["graphDBPath"] == "/tmp/graph.db"
```

```python
# tests/test_acceptance.py
def test_cmd_ingest_registers_session_after_scope_resolution(monkeypatch, tmp_path):
    from orchard import cli as cli_mod

    graph_db = tmp_path / ".orchard" / "graph.db"
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    graph_db.write_text("", encoding="utf-8")

    monkeypatch.setattr("orchard.ingest.lock.try_acquire_graph_db_lock", lambda _path: type("L", (), {"__enter__": lambda self: self, "__exit__": lambda self, exc_type, exc, tb: False})())
    monkeypatch.setattr(cli_mod, "_conn", lambda _path, read_only=False: (_ for _ in ()).throw(SystemExit(0)))

    registered = {}
    monkeypatch.setattr(
        "orchard.ingest.indexstore.register_indexd_session",
        lambda **kwargs: registered.update(kwargs) or {"sessionId": "s1", "registered": True},
    )

    try:
        cli_mod.cmd_ingest([
            "--index-store", "/tmp/DataStore",
            "--project-dir", str(tmp_path),
            "--target", "Zoom,zPSApp",
            "--db", str(graph_db),
        ])
    except SystemExit:
        pass

    assert registered["store_path"] == "/tmp/DataStore"
    assert registered["graph_db_path"] == str(graph_db)
    assert registered["ingest_context"]["targetArgs"] == ["Zoom", "zPSApp"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_indexstore.py::test_indexd_client_register_session_sends_context tests/test_acceptance.py::test_cmd_ingest_registers_session_after_scope_resolution -v`

Expected: FAIL because `register_session` and `register_indexd_session` do not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
# src/orchard/ingest/indexstore.py
class _IndexdClient:
    ...
    def register_session(
        self,
        store_path: str,
        graph_db_path: str,
        ingest_context: dict[str, object],
    ) -> dict[str, object]:
        responses = self._request({
            "id": "register_session",
            "method": "register_session",
            "params": {
                "storePath": store_path,
                "graphDBPath": graph_db_path,
                "ingestContext": ingest_context,
            },
        })
        if not responses or not responses[0].get("ok"):
            raise ConnectionError(f"indexd register_session failed: {responses}")
        return responses[0]["result"]


def register_indexd_session(
    store_path: str,
    graph_db_path: str,
    ingest_context: dict[str, object],
) -> dict[str, object] | None:
    socket_path = _indexd_socket_path()
    if not socket_path or not _ensure_indexd_running(socket_path):
        return None
    return _IndexdClient(socket_path).register_session(
        store_path=store_path,
        graph_db_path=graph_db_path,
        ingest_context=ingest_context,
    )
```

```python
# src/orchard/cli.py (after scope normalization, before heavy ingest work)
from orchard.ingest.indexstore import register_indexd_session

register_indexd_session(
    store_path=index_store,
    graph_db_path=ns.db,
    ingest_context={
        "projectDir": project_dir,
        "indexStorePath": index_store,
        "graphDBPath": ns.db,
        "targetArgs": targets,
        "entryTarget": entry_target,
        "incremental": ns.incremental,
    },
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_indexstore.py::test_indexd_client_register_session_sends_context tests/test_acceptance.py::test_cmd_ingest_registers_session_after_scope_resolution -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/cli.py src/orchard/ingest/indexstore.py tests/test_ingest/test_indexstore.py tests/test_acceptance.py
git commit -m "feat: register ingest sessions with indexd"
```

### Task 3: Extend Swift Daemon Protocol And Session Identity

**Files:**
- Create: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IngestContext.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/Protocol.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift`
- Create: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift`

**Interfaces:**
- Consumes: Python `register_session` RPC payload
- Produces: `RegisterSessionParams`, `RegisterSessionResult`, `SessionManager.registerOrRefreshSession(...)`

- [ ] **Step 1: Write the failing Swift tests**

```swift
// swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift
import XCTest
@testable import orchard_indexd

final class IndexdWatchDrivenIngestTests: XCTestCase {
  func testRegisterSessionReusesSameStoreAndGraphDB() throws {
    let manager = SessionManager()
    let context = IngestContext(
      projectDir: "/tmp/project",
      indexStorePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )

    let first = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: context,
      sourceRoots: ["/tmp/project"],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let second = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: context,
      sourceRoots: ["/tmp/project"],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(first.session.sessionId, second.session.sessionId)
    XCTAssertTrue(second.reused)
  }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests/testRegisterSessionReusesSameStoreAndGraphDB`

Expected: FAIL because `IngestContext` and `registerOrRefreshSession` do not exist.

- [ ] **Step 3: Write the minimal implementation**

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/IngestContext.swift
import Foundation

struct IngestContext: Codable, Equatable {
  let projectDir: String
  let indexStorePath: String
  let graphDBPath: String
  let targetArgs: [String]
  let entryTarget: String
  let incremental: Bool
}
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/Protocol.swift
struct RegisterSessionParams {
  let storePath: String
  let graphDBPath: String
  let ingestContext: IngestContext
  let sourceRoots: [String]
  let targets: [String]
  let dylibPath: String?
}

struct RegisterSessionResult: Encodable {
  let sessionId: String
  let reused: Bool
  let graphDBPath: String
}
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift
func registerOrRefreshSession(
  storePath: String,
  graphDBPath: String,
  ingestContext: IngestContext,
  sourceRoots: [String],
  targets: [String],
  dylibPath: String?
) throws -> (session: IndexdSession, reused: Bool) {
  let keyMaterial = "v1:\(storePath)|\(graphDBPath)"
  let key = SHA256.hash(data: Data(keyMaterial.utf8)).map { String(format: "%02x", $0) }.joined()
  ...
  if let existing = sessions[key] {
    existing.refresh(
      sourceRoots: sourceRoots,
      targets: targets,
      ingestContext: ingestContext
    )
    return (existing, true)
  }
  ...
}
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift
if method == "register_session" {
  let params = requestObject["params"] as? [String: Any] ?? [:]
  let graphDBPath = params["graphDBPath"] as? String ?? ""
  ...
  let result = try manager.registerOrRefreshSession(...)
  try writeLine(DaemonResponse(
    id: id,
    ok: true,
    result: RegisterSessionResult(
      sessionId: result.session.sessionId,
      reused: result.reused,
      graphDBPath: graphDBPath
    ),
    error: Optional<DaemonError>.none
  ), to: output)
  continue
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests/testRegisterSessionReusesSameStoreAndGraphDB`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IngestContext.swift swift/orchard-indexstore-reader/Sources/orchard-indexd/Protocol.swift swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift
git commit -m "feat: add indexd session registration protocol"
```

### Task 4: Add Session-Scoped Watch State And Graph-Scoped Single-Flight Scheduling

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift`
- Modify: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift`

**Interfaces:**
- Consumes: `IngestContext`, registered sessions, existing `IndexStoreDB`
- Produces: `IndexdSession.recordWatchActivity()`, `IndexdSession.handleIngestExit(code: Int32)`, `SessionManager.canStartIngest(graphDBPath: String) -> Bool`

- [ ] **Step 1: Write the failing Swift tests**

```swift
// append to IndexdWatchDrivenIngestTests.swift
func testLatestRegistrationWinsForRememberedContext() throws {
  let manager = SessionManager()
  let first = IngestContext(
    projectDir: "/tmp/project",
    indexStorePath: "/tmp/store",
    graphDBPath: "/tmp/graph.db",
    targetArgs: ["Zoom"],
    entryTarget: "Zoom",
    incremental: true
  )
  let second = IngestContext(
    projectDir: "/tmp/project",
    indexStorePath: "/tmp/store",
    graphDBPath: "/tmp/graph.db",
    targetArgs: ["Zoom", "zPSApp"],
    entryTarget: "Zoom",
    incremental: true
  )

  _ = try manager.registerOrRefreshSession(
    storePath: "/tmp/store",
    graphDBPath: "/tmp/graph.db",
    ingestContext: first,
    sourceRoots: ["/tmp/project"],
    targets: ["Zoom"],
    dylibPath: nil
  )
  let refreshed = try manager.registerOrRefreshSession(
    storePath: "/tmp/store",
    graphDBPath: "/tmp/graph.db",
    ingestContext: second,
    sourceRoots: ["/tmp/project"],
    targets: ["Zoom", "zPSApp"],
    dylibPath: nil
  )

  XCTAssertEqual(refreshed.session.ingestContext.targetArgs, ["Zoom", "zPSApp"])
}
```

```swift
func testSingleFlightIsPerGraphDBPath() throws {
  let manager = SessionManager()
  XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
  XCTAssertFalse(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
  XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/b.db"))
  manager.endGraphDBIngest(graphDBPath: "/tmp/a.db")
  XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests`

Expected: FAIL because remembered context is not stored and graph-db-scoped in-flight coordination does not exist.

- [ ] **Step 3: Write the minimal implementation**

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
final class IndexdSession {
  ...
  private(set) var ingestContext: IngestContext
  private(set) var seenGeneration: UInt64 = 0
  private(set) var ackedGeneration: UInt64 = 0
  private(set) var ingestRunning = false
  private(set) var ingestTargetGeneration: UInt64?

  init(..., ingestContext: IngestContext) throws {
    self.ingestContext = ingestContext
    ...
    self.db = try IndexStoreDB(
      storePath: storePath,
      databasePath: dbPath,
      library: library,
      waitUntilDoneInitializing: false,
      listenToUnitEvents: true
    )
  }

  func refresh(sourceRoots: [String], targets: [String], ingestContext: IngestContext) {
    self.sourceRoots = sourceRoots
    self.targets = targets
    self.ingestContext = ingestContext
  }
}
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift
private var inFlightGraphDBs = Set<String>()

func beginGraphDBIngest(graphDBPath: String) -> Bool {
  lock.lock()
  defer { lock.unlock() }
  return inFlightGraphDBs.insert(graphDBPath).inserted
}

func endGraphDBIngest(graphDBPath: String) {
  lock.lock()
  defer { lock.unlock() }
  inFlightGraphDBs.remove(graphDBPath)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift swift/orchard-indexstore-reader/Sources/orchard-indexd/SessionManager.swift swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift
git commit -m "feat: add session watch state and graph single-flight"
```

### Task 5: Launch Background `orchard ingest` From The Daemon

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift`
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`
- Modify: `src/orchard/ingest/indexstore.py`
- Modify: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift`

**Interfaces:**
- Consumes: registered `IngestContext`, `LOCK_BUSY_EXIT_CODE`, graph-db single-flight helpers
- Produces: daemon child-spawn path, debounce scheduling, retry scheduling on lock-busy only

- [ ] **Step 1: Write the failing tests**

```swift
// append to IndexdWatchDrivenIngestTests.swift
func testLockBusySchedulesRetryButOtherFailuresDoNot() throws {
  let session = try makeTestSession()
  session.beginIngest(targetGeneration: 3)
  session.handleIngestExit(code: 23)
  XCTAssertTrue(session.retryScheduled)

  session.beginIngest(targetGeneration: 4)
  session.handleIngestExit(code: 1)
  XCTAssertFalse(session.retryScheduledForLastExit)
}
```

```python
# tests/test_ingest/test_indexstore.py
def test_start_indexd_process_passes_cli_path(monkeypatch, tmp_path):
    from orchard.ingest.indexstore import _start_indexd_process

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setattr("orchard.ingest.indexstore._indexd_path", lambda: "/tmp/orchard-indexd")
    monkeypatch.setattr("orchard.ingest.indexstore._cli_path", lambda: "/tmp/orchard")
    monkeypatch.setattr("orchard.ingest.indexstore._indexd_log_path", lambda: str(tmp_path / "indexd.log"))
    monkeypatch.setattr("orchard.ingest.indexstore._indexd_pid_path", lambda _socket: str(tmp_path / "indexd.pid"))
    monkeypatch.setattr("orchard.ingest.indexstore._cleanup_stale_indexd_socket", lambda *_args, **_kwargs: None)

    _start_indexd_process("/tmp/orchard-indexd.sock")
    assert "--orchard-cli" in captured["argv"]
    assert "/tmp/orchard" in captured["argv"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest/test_indexstore.py::test_start_indexd_process_passes_cli_path -v && swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests/testLockBusySchedulesRetryButOtherFailuresDoNot`

Expected: FAIL because the daemon does not receive the CLI path and sessions do not schedule retries from child exit codes.

- [ ] **Step 3: Write the minimal implementation**

```python
# src/orchard/ingest/indexstore.py
return subprocess.Popen(
    [_indexd_path(), "--socket", socket_path, "--pid-file", pid_path, "--orchard-cli", _cli_path()],
    stdout=log_handle,
    stderr=log_handle,
    text=True,
    start_new_session=True,
)
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift
private func parseOrchardCLIPath() -> String {
  let args = CommandLine.arguments
  if let index = args.firstIndex(of: "--orchard-cli"), index + 1 < args.count {
    return args[index + 1]
  }
  return "orchard"
}
```

```swift
// swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
func maybeScheduleBackgroundIngest(
  orchardCLIPath: String,
  beginInFlight: () -> Bool,
  endInFlight: @escaping () -> Void
) {
  guard ackedGeneration < seenGeneration else { return }
  guard beginInFlight() else { return }
  ingestRunning = true
  ingestTargetGeneration = seenGeneration

  let process = Process()
  process.executableURL = URL(fileURLWithPath: orchardCLIPath)
  process.arguments = [
    "ingest",
    "--index-store", ingestContext.indexStorePath,
    "--project-dir", ingestContext.projectDir,
    "--target", ingestContext.targetArgs.joined(separator: ","),
    "--db", ingestContext.graphDBPath,
    ingestContext.incremental ? "--incremental" : "--full",
  ]
  process.terminationHandler = { [weak self] proc in
    self?.handleIngestExit(code: proc.terminationStatus)
    endInFlight()
  }
  try? process.run()
}

func handleIngestExit(code: Int32) {
  ingestRunning = false
  if code == 0, let target = ingestTargetGeneration {
    ackedGeneration = max(ackedGeneration, target)
    ingestTargetGeneration = nil
    scheduleShortDebounceIfNeeded()
    return
  }
  if code == 23 {
    scheduleRetry()
    return
  }
  ingestTargetGeneration = nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest/test_indexstore.py::test_start_indexd_process_passes_cli_path -v && swift test --package-path swift/orchard-indexstore-reader --filter IndexdWatchDrivenIngestTests`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/ingest/indexstore.py swift/orchard-indexstore-reader/Sources/orchard-indexd/DaemonMain.swift swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift
git commit -m "feat: add daemon-driven background ingest scheduling"
```

### Task 6: End-To-End Verification And Regression Coverage

**Files:**
- Modify: `tests/test_ingest/test_indexstore.py`
- Modify: `tests/test_acceptance.py`
- Modify: `swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift`

**Interfaces:**
- Consumes: all previous task outputs
- Produces: stable regression suite covering session registration, locking, and background retry behavior

- [ ] **Step 1: Write the final failing regression tests**

```python
# tests/test_ingest/test_indexstore.py
def test_register_session_returns_none_when_indexd_unavailable(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore._indexd_socket_path", lambda: "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda _socket: False)
    from orchard.ingest.indexstore import register_indexd_session
    assert register_indexd_session("/tmp/store", "/tmp/graph.db", {"targetArgs": ["Zoom"]}) is None
```

```python
# tests/test_acceptance.py
def test_cmd_ingest_emits_lock_busy_marker(monkeypatch, capsys, tmp_path):
    from orchard import cli as cli_mod
    graph_db = tmp_path / ".orchard" / "graph.db"
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    graph_db.write_text("", encoding="utf-8")
    monkeypatch.setattr("orchard.ingest.lock.try_acquire_graph_db_lock", lambda _path: None)
    try:
      cli_mod.cmd_ingest(["--index-store", "/tmp/store", "--project-dir", str(tmp_path), "--target", "Zoom", "--db", str(graph_db)])
    except SystemExit:
      pass
    assert "INGEST_LOCK_BUSY" in capsys.readouterr().err
```

- [ ] **Step 2: Run the targeted regression tests to verify current gaps**

Run: `pytest tests/test_ingest/test_indexstore.py::test_register_session_returns_none_when_indexd_unavailable tests/test_acceptance.py::test_cmd_ingest_emits_lock_busy_marker -v`

Expected: FAIL until the final glue from previous tasks is complete.

- [ ] **Step 3: Finish any missing glue and run the full focused suites**

```bash
pytest tests/test_ingest/test_indexstore.py tests/test_acceptance.py -q
swift test --package-path swift/orchard-indexstore-reader
```

Expected:

- `pytest` passes for `indexstore` and acceptance coverage
- `swift test` passes for daemon protocol and scheduler behavior

- [ ] **Step 4: Smoke-test the packaged binaries path**

```bash
pytest tests/test_ingest/test_indexstore_real_cli.py::test_installed_wheel_cli_can_ingest_minimal_index -q
```

Expected: PASS, confirming the wheel-installed Python package can still resolve both `orchard-indexd` and `orchard-indexstore-reader`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ingest/test_indexstore.py tests/test_acceptance.py swift/orchard-indexstore-reader/Tests/orchard-indexstore-readerTests/IndexdWatchDrivenIngestTests.swift
git commit -m "test: add watch-driven ingest regressions"
```

## Self-Review

### Spec coverage

- Session bootstrap and register-or-refresh RPC are implemented by Task 2 and Task 3.
- Last-writer-wins remembered context semantics are implemented by Task 3 and Task 4.
- Graph-db-scoped CLI lock and `LOCK_BUSY` exit semantics are implemented by Task 1.
- Graph-db-scoped single-flight scheduling is implemented by Task 4 and Task 5.
- Watch-driven background ingest and lock-busy retry are implemented by Task 5.
- End-to-end regression coverage is implemented by Task 6.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Every task names concrete files, interfaces, commands, and expected outcomes.

### Type consistency

- Python session registration uses `register_indexd_session(...)` and `_IndexdClient.register_session(...)` consistently.
- Swift session bootstrap uses `IngestContext`, `RegisterSessionParams`, and `registerOrRefreshSession(...)` consistently.
- Lock-busy signaling uses `LOCK_BUSY_EXIT_CODE = 23` consistently across CLI and daemon scheduling.
