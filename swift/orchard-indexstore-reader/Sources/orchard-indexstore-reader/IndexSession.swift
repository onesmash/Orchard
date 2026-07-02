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
