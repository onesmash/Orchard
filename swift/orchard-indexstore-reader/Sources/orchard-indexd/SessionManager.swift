import Foundation
import CryptoKit

final class SessionManager {
  private var sessions: [String: IndexdSession] = [:]
  private var inFlightGraphDBs = Set<String>()
  private let lock = NSLock()

  func getOrCreateSession(
    storePath: String,
    sourceRoots: [String],
    targets: [String],
    dylibPath: String?
  ) throws -> (session: IndexdSession, reused: Bool) {
    let key = SHA256.hash(data: Data("v2:\(storePath)".utf8)).map { String(format: "%02x", $0) }.joined()
    lock.lock()
    defer { lock.unlock() }

    if let existing = sessions[key] {
      existing.update(sourceRoots: sourceRoots, targets: targets)
      return (existing, true)
    }

    let session = try IndexdSession(
      sessionId: key,
      storePath: storePath,
      sourceRoots: sourceRoots,
      targets: targets,
      dylibPath: dylibPath,
      ingestContext: nil
    )
    sessions[key] = session
    return (session, false)
  }

  func registerOrRefreshSession(
    storePath: String,
    graphDBPath: String,
    ingestContext: IngestContext,
    sourceRoots: [String],
    targets: [String],
    dylibPath: String?
  ) throws -> (session: IndexdSession, reused: Bool) {
    let key = sessionKey(namespace: "register.v1", parts: [storePath, graphDBPath])
    lock.lock()
    defer { lock.unlock() }

    if let existing = sessions[key] {
      existing.refresh(
        sourceRoots: sourceRoots,
        targets: targets,
        ingestContext: ingestContext
      )
      return (existing, true)
    }

    let session = try IndexdSession(
      sessionId: key,
      storePath: storePath,
      sourceRoots: sourceRoots,
      targets: targets,
      dylibPath: dylibPath,
      ingestContext: ingestContext
    )
    sessions[key] = session
    return (session, false)
  }

  func session(id: String) -> IndexdSession? {
    lock.lock()
    defer { lock.unlock() }
    return sessions[id]
  }

  func beginGraphDBIngest(graphDBPath: String) -> Bool {
    let key = canonicalSessionPath(graphDBPath)
    lock.lock()
    defer { lock.unlock() }
    return inFlightGraphDBs.insert(key).inserted
  }

  func endGraphDBIngest(graphDBPath: String) {
    let key = canonicalSessionPath(graphDBPath)
    lock.lock()
    defer { lock.unlock() }
    inFlightGraphDBs.remove(key)
  }
}

private func sessionKey(namespace: String, parts: [String]) -> String {
  let normalizedParts = parts.map(canonicalSessionPath)
  let keyMaterial = ([namespace] + normalizedParts).joined(separator: "|")
  return SHA256.hash(data: Data(keyMaterial.utf8)).map { String(format: "%02x", $0) }.joined()
}

private func canonicalSessionPath(_ path: String) -> String {
  guard !path.isEmpty else {
    return path
  }
  return URL(fileURLWithPath: path)
    .standardizedFileURL
    .resolvingSymlinksInPath()
    .path
}
