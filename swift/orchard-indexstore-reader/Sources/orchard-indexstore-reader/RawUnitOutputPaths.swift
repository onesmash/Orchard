import Dispatch
import Foundation
import IndexStore
import IndexStoreDB

struct RawUnitOutputPathMapping: Hashable {
  let unitName: String
  let mainFile: String
  let outputFile: String
}

private func _runAsync<T>(_ operation: @escaping () async throws -> T) throws -> T {
  let semaphore = DispatchSemaphore(value: 0)
  var result: Result<T, Error>!
  Task {
    do {
      result = .success(try await operation())
    } catch {
      result = .failure(error)
    }
    semaphore.signal()
  }
  semaphore.wait()
  return try result.get()
}

func collectRawUnitOutputPathMappings(
  indexStorePath: String,
  dylibPath: String,
  db: IndexStoreDB,
  filePaths: [String]
) throws -> [RawUnitOutputPathMapping] {
  let uniqueUnitNames = Set(filePaths.flatMap { db.unitNamesContainingFile(path: $0) })
  let rawLibrary = try _runAsync {
    try await IndexStoreLibrary.at(dylibPath: URL(fileURLWithPath: dylibPath))
  }
  let rawStore = try rawLibrary.indexStore(at: URL(fileURLWithPath: indexStorePath, isDirectory: true))

  var mappings: [RawUnitOutputPathMapping] = []
  mappings.reserveCapacity(uniqueUnitNames.count)

  for unitName in uniqueUnitNames.sorted() {
    let unit = try rawStore.unit(named: unitName)
    guard unit.hasMainFile else { continue }

    let mainFile = unit.mainFile.string
    let outputFile = unit.outputFile.string
    guard !mainFile.isEmpty, !outputFile.isEmpty else { continue }

    mappings.append(
      RawUnitOutputPathMapping(
        unitName: unitName,
        mainFile: mainFile,
        outputFile: outputFile
      )
    )
  }

  return mappings
}
