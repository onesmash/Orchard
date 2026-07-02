import Foundation
import CryptoKit

private struct ManagedSession {
  let session: IndexdSession
  var lastActivityAt: Date
}

final class SessionManager {
  private var sessions: [String: ManagedSession] = [:]
  private var inFlightGraphDBs = Set<String>()
  private let lock = NSLock()

  func getOrCreateSession(
    storePath: String,
    sourceRoots: [String],
    targets: [String],
    dylibPath: String?
  ) throws -> (session: IndexdSession, reused: Bool) {
    let key = storeSessionKey(storePath)
    lock.lock()
    defer { lock.unlock() }

    if var existing = sessions[key] {
      existing.session.update(sourceRoots: sourceRoots, targets: targets)
      existing.lastActivityAt = Date()
      sessions[key] = existing
      return (existing.session, true)
    }

    let session = try IndexdSession(
      sessionId: key,
      storePath: storePath,
      sourceRoots: sourceRoots,
      targets: targets,
      dylibPath: dylibPath,
      ingestContext: nil
    )
    sessions[key] = ManagedSession(session: session, lastActivityAt: Date())
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
    let key = storeSessionKey(storePath)
    lock.lock()
    defer { lock.unlock() }

    if var existing = sessions[key] {
      existing.session.refresh(
        sourceRoots: sourceRoots,
        targets: targets,
        ingestContext: ingestContext
      )
      existing.lastActivityAt = Date()
      sessions[key] = existing
      return (existing.session, true)
    }

    let session = try IndexdSession(
      sessionId: key,
      storePath: storePath,
      sourceRoots: sourceRoots,
      targets: targets,
      dylibPath: dylibPath,
      ingestContext: ingestContext
    )
    sessions[key] = ManagedSession(session: session, lastActivityAt: Date())
    return (session, false)
  }

  func session(id: String) -> IndexdSession? {
    lock.lock()
    defer { lock.unlock() }
    guard var managedSession = sessions[id] else {
      return nil
    }
    managedSession.lastActivityAt = Date()
    sessions[id] = managedSession
    return managedSession.session
  }

  func evictIdleSessions(idleForAtLeast seconds: TimeInterval, now: Date = Date()) -> Int {
    lock.lock()
    defer { lock.unlock() }

    let evictedKeys = sessions.compactMap { entry -> String? in
      let (key, managedSession) = entry
      guard now.timeIntervalSince(managedSession.lastActivityAt) >= seconds else {
        return nil
      }
      let snapshot = managedSession.session.snapshot()
      guard snapshot.seenGeneration == snapshot.ackedGeneration,
            !snapshot.ingestRunning,
            !snapshot.retryScheduled,
            !snapshot.debounceScheduled else {
        return nil
      }
      return key
    }
    evictedKeys.forEach { sessions.removeValue(forKey: $0) }
    return evictedKeys.count
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

private func storeSessionKey(_ storePath: String) -> String {
  let normalizedStorePath = canonicalSessionPath(storePath)
  return SHA256.hash(data: Data("v2:\(normalizedStorePath)".utf8)).map { String(format: "%02x", $0) }.joined()
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
