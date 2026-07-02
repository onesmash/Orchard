import Foundation
import IndexStoreDB

private let sourceExtensions: Set<String> = [
  "swift", "m", "mm", "c", "cc", "cpp", "cxx", "c++",
  "h", "hh", "hpp", "hxx",
]

func resolveEffectiveSourceRoots(
  explicitSourceRoots: [String],
  targets: [String],
  storePath: String
) -> [String] {
  if !explicitSourceRoots.isEmpty {
    return explicitSourceRoots
  }
  if !targets.isEmpty {
    return (try? sourceRootsForTargets(indexStorePath: storePath, targets: targets)) ?? []
  }
  return []
}

func enumerateSourceFiles(
  sourceRoots: [String],
  underRoot: (String) -> Bool
) -> [String] {
  let fm = FileManager.default
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

func discoverFilePaths(
  db: IndexStoreDB
) -> [String] {
  var seen = Set<String>()
  for name in db.allSymbolNames() {
    for occ in db.canonicalOccurrences(ofName: name) {
      let path = occ.location.path
      if !path.isEmpty {
        seen.insert(path)
      }
    }
  }
  return Array(seen)
}

func filterChangedFiles(
  db: IndexStoreDB,
  allFiles: [String],
  since: Double
) -> (changedFiles: [String], allFiles: [String], skippedCount: Int) {
  let sinceDate = Date(timeIntervalSince1970: since)
  let changedFiles = allFiles.filter { filePath in
    guard let unitDate = db.dateOfLatestUnitFor(filePath: filePath) else {
      return true
    }
    return unitDate > sinceDate
  }
  return (changedFiles, allFiles, allFiles.count - changedFiles.count)
}
