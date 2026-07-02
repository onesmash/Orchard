import IndexStoreDB
import XCTest
@testable import orchard_indexd

final class IndexdWatchDrivenIngestTests: XCTestCase {
  private final class SynchronizedCounter {
    private let lock = NSLock()
    private var storage = 0

    @discardableResult
    func increment() -> Int {
      lock.lock()
      defer { lock.unlock() }
      storage += 1
      return storage
    }

    var value: Int {
      lock.lock()
      defer { lock.unlock() }
      return storage
    }
  }

  private func waitUntil(
    timeout: TimeInterval = 1.0,
    pollInterval: TimeInterval = 0.01,
    _ condition: @escaping () -> Bool
  ) {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if condition() {
        return
      }
      RunLoop.current.run(until: Date().addingTimeInterval(pollInterval))
    }
  }

  private func registerPayload(
    storePath: String = "/tmp/store",
    graphDBPath: String = "/tmp/graph.db",
    contextOverrides: [String: Any] = [:]
  ) -> [String: Any] {
    var context: [String: Any] = [
      "projectDir": "/tmp/project",
      "indexStorePath": storePath,
      "graphDBPath": graphDBPath,
      "targetArgs": ["Zoom"],
      "entryTarget": "Zoom",
      "incremental": true,
    ]
    for (key, value) in contextOverrides {
      context[key] = value
    }
    return [
      "storePath": storePath,
      "graphDBPath": graphDBPath,
      "context": context,
    ]
  }

  private func buildMinimalSwiftIndex(tmp: URL) throws -> URL {
    let storePath = tmp.appendingPathComponent("idx", isDirectory: true)
    let srcDir = tmp.appendingPathComponent("src", isDirectory: true)
    let sourceFile = srcDir.appendingPathComponent("Lib.swift")
    let unitOutputPath = srcDir.appendingPathComponent("Lib.o")
    let dylibPath = srcDir.appendingPathComponent("libtest.dylib")

    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: srcDir, withIntermediateDirectories: true)
    try """
    public func callee() -> Int { return 1 }
    public func caller() -> Int { return callee() }
    """.write(to: sourceFile, atomically: true, encoding: .utf8)

    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/swiftc")
    process.arguments = [
      "-index-store-path", storePath.path,
      "-index-unit-output-path", unitOutputPath.path,
      sourceFile.path,
      "-emit-library",
      "-o", dylibPath.path,
    ]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    return storePath
  }

  private func makeTestSession() throws -> IndexdSession {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    addTeardownBlock {
      try? FileManager.default.removeItem(at: tmp)
    }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let context = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )

    return try IndexdSession(
      sessionId: UUID().uuidString,
      storePath: storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil,
      ingestContext: context
    )
  }

  func testRegisterOrRefreshSessionReusesSameSessionForSameStoreAndGraphPair() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "Zoom",
      incremental: true
    )

    let first = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let second = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(first.session.sessionId, second.session.sessionId)
    XCTAssertFalse(first.reused)
    XCTAssertTrue(second.reused)
  }

  func testRegisterOrRefreshSessionReplacesRememberedContextOnReuse() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "zPSApp",
      incremental: false
    )

    _ = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let refreshed = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    let snapshot = refreshed.session.snapshot()
    XCTAssertEqual(snapshot.ingestContext, secondContext)
    XCTAssertEqual(snapshot.sourceRoots, [tmp.path])
    XCTAssertEqual(snapshot.targets, ["Zoom", "zPSApp"])
    XCTAssertEqual(snapshot.seenGeneration, 0)
    XCTAssertEqual(snapshot.ackedGeneration, 0)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testLatestRegistrationWinsForRememberedContextOnReusedSession() throws {
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: "/tmp/project",
      indexStorePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: "/tmp/project",
      indexStorePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "zPSApp",
      incremental: false
    )

    let first = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: ["/tmp/project"],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let refreshed = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: ["/tmp/project/next"],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(first.session.sessionId, refreshed.session.sessionId)
    let snapshot = refreshed.session.snapshot()
    XCTAssertEqual(snapshot.ingestContext, secondContext)
    XCTAssertEqual(snapshot.ingestContext?.targetArgs, ["Zoom", "zPSApp"])
    XCTAssertEqual(snapshot.sourceRoots, ["/tmp/project/next"])
    XCTAssertEqual(snapshot.targets, ["Zoom", "zPSApp"])
  }

  func testSingleFlightIsScopedPerGraphDBPath() {
    let manager = SessionManager()

    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
    XCTAssertFalse(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/b.db"))

    manager.endGraphDBIngest(graphDBPath: "/tmp/a.db")

    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
  }

  func testRegisterSessionBackfillsPendingWorkAfterBackgroundIngestConfiguration() throws {
    let session = try makeTestSession()
    let beginInFlightCalls = SynchronizedCounter()

    waitUntil {
      !session.snapshot().debounceScheduled
    }
    let baselineGeneration = session.snapshot().seenGeneration
    session.recordWatchActivity()
    var snapshot = session.snapshot()
    XCTAssertEqual(snapshot.seenGeneration, baselineGeneration + 1)
    XCTAssertFalse(snapshot.debounceScheduled)

    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: {
        _ = beginInFlightCalls.increment()
        return false
      },
      endInFlight: {}
    )

    waitUntil {
      session.snapshot().hasIngestContext
    }

    snapshot = session.snapshot()
    XCTAssertGreaterThanOrEqual(snapshot.seenGeneration, baselineGeneration + 1)
    waitUntil {
      beginInFlightCalls.value > 0
    }

    snapshot = session.snapshot()
    XCTAssertGreaterThan(snapshot.seenGeneration, baselineGeneration)
    XCTAssertGreaterThan(beginInFlightCalls.value, 0)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testGraphDBSingleFlightConflictPreservesPendingWorkAndRetries() throws {
    let session = try makeTestSession()
    let beginInFlightCalls = SynchronizedCounter()

    session.recordWatchActivity()
    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: {
        beginInFlightCalls.increment() > 1
      },
      endInFlight: {}
    )

    waitUntil(timeout: 2.5) {
      beginInFlightCalls.value >= 2 && !session.snapshot().ingestRunning
    }

    let snapshot = session.snapshot()
    XCTAssertGreaterThanOrEqual(beginInFlightCalls.value, 2)
    XCTAssertGreaterThan(snapshot.seenGeneration, snapshot.ackedGeneration)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testLockBusySchedulesRetryButOtherFailuresDoNot() throws {
    let session = try makeTestSession()

    XCTAssertTrue(session.beginIngest(targetGeneration: 3))
    session.handleIngestExit(code: 23)

    var snapshot = session.snapshot()
    XCTAssertTrue(snapshot.retryScheduled)
    XCTAssertTrue(snapshot.retryScheduledForLastExit)
    XCTAssertFalse(snapshot.ingestRunning)

    XCTAssertTrue(session.beginIngest(targetGeneration: 4))
    session.handleIngestExit(code: 1)

    snapshot = session.snapshot()
    XCTAssertFalse(snapshot.retryScheduled)
    XCTAssertFalse(snapshot.retryScheduledForLastExit)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testDecodeRegisterSessionParamsRejectsMismatchedPaths() throws {
    let payload = registerPayload(
      contextOverrides: [
        "indexStorePath": "/tmp/other-store",
        "graphDBPath": "/tmp/other-graph.db",
      ]
    )

    do {
      _ = try decodeRegisterSessionParams(from: payload)
      XCTFail("expected mismatched paths to be rejected")
    } catch let error as RegisterSessionDecodeError {
      XCTAssertEqual(error.code, "mismatched_store_path")
    }
  }

  func testDecodeRegisterSessionParamsRejectsMalformedContext() throws {
    let payload: [String: Any] = [
      "storePath": "/tmp/store",
      "graphDBPath": "/tmp/graph.db",
      "context": [
        "projectDir": "/tmp/project",
        "indexStorePath": "/tmp/store",
        "targetArgs": ["Zoom"],
        "entryTarget": "Zoom",
        "incremental": true,
      ],
    ]

    do {
      _ = try decodeRegisterSessionParams(from: payload)
      XCTFail("expected malformed context to be rejected")
    } catch let error as RegisterSessionDecodeError {
      XCTAssertEqual(error.code, "invalid_context")
    }
  }
}
