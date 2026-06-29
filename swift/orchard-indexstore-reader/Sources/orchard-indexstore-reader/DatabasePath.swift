import CryptoKit
import Foundation

private let indexStoreDBSchemaVersion = "v2"

func persistentDatabasePath(
  storePath: String,
  environment: [String: String] = ProcessInfo.processInfo.environment
) -> String {
  let baseDir: URL
  if let override = environment["ORCHARD_INDEXSTOREDB_DIR"], !override.isEmpty {
    baseDir = URL(fileURLWithPath: override, isDirectory: true)
  } else if let home = environment["HOME"], !home.isEmpty {
    baseDir = URL(fileURLWithPath: home, isDirectory: true)
      .appendingPathComponent(".orchard/indexstore-db", isDirectory: true)
  } else {
    baseDir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
      .appendingPathComponent("orchard-indexstore-db", isDirectory: true)
  }

  let cacheKey = "\(indexStoreDBSchemaVersion):\(storePath)"
  let digest = SHA256.hash(data: Data(cacheKey.utf8))
  let key = digest.map { String(format: "%02x", $0) }.joined()
  return baseDir.appendingPathComponent(key, isDirectory: true).path
}
