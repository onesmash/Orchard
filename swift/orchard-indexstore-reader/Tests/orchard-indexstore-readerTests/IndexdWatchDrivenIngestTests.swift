import IndexStoreDB
import XCTest
@testable import orchard_indexd

final class IndexdWatchDrivenIngestTests: XCTestCase {
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

    XCTAssertEqual(refreshed.session.ingestContext, secondContext)
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
