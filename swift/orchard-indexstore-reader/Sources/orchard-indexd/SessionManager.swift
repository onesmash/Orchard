import Foundation
import CryptoKit

final class SessionManager {
  private var sessions: [String: IndexdSession] = [:]
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
      dylibPath: dylibPath
    )
    sessions[key] = session
    return (session, false)
  }

  func session(id: String) -> IndexdSession? {
    lock.lock()
    defer { lock.unlock() }
    return sessions[id]
  }
}
