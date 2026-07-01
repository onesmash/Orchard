import XCTest
@testable import orchard_indexstore_reader

final class DatabasePathTests: XCTestCase {
  func testPersistentDatabasePathIsStableForSameStore() {
    let env = ["HOME": "/Users/tester"]
    let first = persistentDatabasePath(
      storePath: "/tmp/DerivedData/A/Index.noindex/DataStore",
      environment: env
    )
    let second = persistentDatabasePath(
      storePath: "/tmp/DerivedData/A/Index.noindex/DataStore",
      environment: env
    )

    XCTAssertEqual(first, second)
    XCTAssertFalse(first.contains("orchard-indexstore-db-"))
  }

  func testPersistentDatabasePathDiffersAcrossStores() {
    let env = ["HOME": "/Users/tester"]
    let first = persistentDatabasePath(
      storePath: "/tmp/DerivedData/A/Index.noindex/DataStore",
      environment: env
    )
    let second = persistentDatabasePath(
      storePath: "/tmp/DerivedData/B/Index.noindex/DataStore",
      environment: env
    )

    XCTAssertNotEqual(first, second)
  }

  func testBufferedLineWriterFlushesLinesInOrder() {
    var writes: [String] = []
    var writer = BufferedLineWriter(
      flushThresholdBytes: 8,
      sink: { data in
        writes.append(String(decoding: data, as: UTF8.self))
      }
    )

    writer.writeLine("abc")
    XCTAssertEqual(writes, [])

    writer.writeLine("defg")
    XCTAssertEqual(writes, ["abc\ndefg\n"])

    writer.writeLine("tail")
    writer.flush()

    XCTAssertEqual(writes, ["abc\ndefg\n", "tail\n"])
  }

  func testRelationDedupKeyHashesEquivalentRelationsOnce() {
    let first = RelationDedupKey(
      fromUSR: "s:callee",
      toUSR: "s:caller",
      role: "calledBy",
      occurrenceRole: "call"
    )
    let second = RelationDedupKey(
      fromUSR: "s:callee",
      toUSR: "s:caller",
      role: "calledBy",
      occurrenceRole: "call"
    )
    let third = RelationDedupKey(
      fromUSR: "s:callee",
      toUSR: "s:caller",
      role: "calledBy",
      occurrenceRole: "reference"
    )

    XCTAssertEqual(Set([first, second]).count, 1)
    XCTAssertEqual(Set([first, third]).count, 2)
  }
}
