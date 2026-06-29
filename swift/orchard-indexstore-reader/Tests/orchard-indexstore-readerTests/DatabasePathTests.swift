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
}
