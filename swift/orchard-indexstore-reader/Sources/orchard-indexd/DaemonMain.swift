import Foundation
import Darwin

private let daemonProtocolVersion = 1

@main
struct OrchardIndexdMain {
  static func main() throws {
    let socketPath = parseSocketPath()
    let pidFilePath = parsePIDFilePath(socketPath: socketPath)
    let runtimeInfo = collectRuntimeInfo()
    try? FileManager.default.removeItem(atPath: socketPath)

    let serverFD = socket(AF_UNIX, SOCK_STREAM, 0)
    guard serverFD >= 0 else {
      throw POSIXError(.EIO)
    }
    defer {
      close(serverFD)
      unlink(socketPath)
      if let pidFilePath {
        try? FileManager.default.removeItem(atPath: pidFilePath)
      }
    }

    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let maxLen = MemoryLayout.size(ofValue: addr.sun_path)
    socketPath.withCString { src in
      withUnsafeMutablePointer(to: &addr.sun_path) { dst in
        let rawDst = UnsafeMutableRawPointer(dst).assumingMemoryBound(to: CChar.self)
        strncpy(rawDst, src, maxLen - 1)
      }
    }

    let bindResult = withUnsafePointer(to: &addr) { ptr in
      ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        bind(serverFD, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
      }
    }
    guard bindResult == 0 else {
      throw POSIXError(.EADDRINUSE)
    }
    guard listen(serverFD, 8) == 0 else {
      throw POSIXError(.EIO)
    }

    if let pidFilePath {
      try writePIDFile(path: pidFilePath, pid: runtimeInfo.pid)
    }

    let manager = SessionManager()

    while true {
      let clientFD = accept(serverFD, nil, nil)
      guard clientFD >= 0 else {
        continue
      }
      let input = FileHandle(fileDescriptor: clientFD, closeOnDealloc: true)
      let output = input
      let data = input.readDataToEndOfFile()
      guard let text = String(data: data, encoding: .utf8) else {
        continue
      }

      for line in text.split(separator: "\n") {
        if line.isEmpty {
          continue
        }
        guard let lineData = String(line).data(using: .utf8),
              let requestObject = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
              let id = requestObject["id"] as? String,
              let method = requestObject["method"] as? String else {
          continue
        }

        if method == "shutdown" {
          return
        }

        if method == "ping" {
          try writeLine(DaemonResponse(
            id: id,
            ok: true,
            result: PingResult(
              status: "ok",
              protocolVersion: daemonProtocolVersion,
              pid: runtimeInfo.pid,
              executablePath: runtimeInfo.executablePath,
              binarySize: runtimeInfo.binarySize,
              binaryMTimeNs: runtimeInfo.binaryMTimeNs
            ),
            error: Optional<DaemonError>.none
          ), to: output)
          continue
        }

        if method == "register_session" {
          let params = requestObject["params"] as? [String: Any] ?? [:]
          let registerParams: RegisterSessionParams
          do {
            registerParams = try decodeRegisterSessionParams(from: params)
          } catch let error as RegisterSessionDecodeError {
            try writeLine(DaemonResponse<RegisterSessionResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: error.code, message: error.message)
            ), to: output)
            continue
          }

          do {
            let result = try manager.registerOrRefreshSession(
              storePath: registerParams.storePath,
              graphDBPath: registerParams.graphDBPath,
              ingestContext: registerParams.context,
              sourceRoots: [],
              targets: [],
              dylibPath: nil
            )
            try writeLine(DaemonResponse(
              id: id,
              ok: true,
              result: RegisterSessionResult(
                sessionId: result.session.sessionId,
                reused: result.reused,
                graphDBPath: registerParams.graphDBPath
              ),
              error: Optional<DaemonError>.none
            ), to: output)
          } catch {
            try writeLine(DaemonResponse<RegisterSessionResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "register_session_failed", message: String(describing: error))
            ), to: output)
          }
          continue
        }

        if method == "warm" {
          let params = requestObject["params"] as? [String: Any] ?? [:]
          let storePath = params["storePath"] as? String ?? ""
          let sourceRoots = params["sourceRoots"] as? [String] ?? []
          let targets = params["targets"] as? [String] ?? []
          let dylibPath = params["dylibPath"] as? String

          if storePath.isEmpty {
            try writeLine(DaemonResponse<WarmResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "missing_store_path", message: "storePath is required")
            ), to: output)
            continue
          }

          do {
            let result = try manager.getOrCreateSession(
              storePath: storePath,
              sourceRoots: sourceRoots,
              targets: targets,
              dylibPath: dylibPath
            )
            try writeLine(DaemonResponse(
              id: id,
              ok: true,
              result: WarmResult(
                sessionId: result.session.sessionId,
                reused: result.reused,
                dbPath: result.session.dbPath
              ),
              error: Optional<DaemonError>.none
            ), to: output)
          } catch {
            try writeLine(DaemonResponse<WarmResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "warm_failed", message: String(describing: error))
            ), to: output)
          }
          continue
        }

        if method == "scan" {
          let params = requestObject["params"] as? [String: Any] ?? [:]
          let sessionID = params["sessionId"] as? String ?? ""
          let emitOccurrences = params["emitOccurrences"] as? Bool ?? false
          let incrementalSince = params["incrementalSince"] as? Double

          guard let session = manager.session(id: sessionID) else {
            try writeLine(DaemonResponse<WarmResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "missing_session", message: "session not found")
            ), to: output)
            continue
          }

          session.poll()
          let scanned = session.scan(
            incrementalSince: incrementalSince,
            emitOccurrences: emitOccurrences
          )

          try writeRaw([
            "id": id,
            "stream": "start",
            "ok": true,
          ], to: output)

          for chunk in scanned.records.chunked(into: 500) {
            try writeRaw([
              "id": id,
              "stream": "chunk",
              "records": chunk,
            ], to: output)
          }

          try writeRaw([
            "id": id,
            "stream": "end",
            "summary": [
              "symbols": scanned.summary.symbols,
              "relations": scanned.summary.relations,
              "changedFiles": scanned.summary.changedFiles,
              "allFiles": scanned.summary.allFiles,
            ],
            "fileStatus": scanned.fileStatus,
          ], to: output)
          continue
        }

        if method == "list_files" {
          let params = requestObject["params"] as? [String: Any] ?? [:]
          let sessionID = params["sessionId"] as? String ?? ""
          guard let session = manager.session(id: sessionID) else {
            try writeLine(DaemonResponse<WarmResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "missing_session", message: "session not found")
            ), to: output)
            continue
          }
          try writeRaw([
            "id": id,
            "ok": true,
            "result": [
              "files": session.listFilesResponse(),
            ],
          ], to: output)
          continue
        }

        if method == "dump_unit_output_paths" {
          let params = requestObject["params"] as? [String: Any] ?? [:]
          let sessionID = params["sessionId"] as? String ?? ""
          guard let session = manager.session(id: sessionID) else {
            try writeLine(DaemonResponse<WarmResult>(
              id: id,
              ok: false,
              result: nil,
              error: DaemonError(code: "missing_session", message: "session not found")
            ), to: output)
            continue
          }
          try writeRaw([
            "id": id,
            "ok": true,
            "result": [
              "output_path_mappings": session.dumpUnitOutputPathsResponse(),
            ],
          ], to: output)
          continue
        }

        try writeLine(DaemonResponse<WarmResult>(
          id: id,
          ok: false,
          result: nil,
          error: DaemonError(code: "unknown_method", message: method)
        ), to: output)
      }
    }
  }
}

private struct RuntimeInfo {
  let pid: Int32
  let executablePath: String
  let binarySize: UInt64
  let binaryMTimeNs: UInt64
}

struct RegisterSessionDecodeError: Error {
  let code: String
  let message: String
}

func decodeRegisterSessionParams(from object: [String: Any]) throws -> RegisterSessionParams {
  let storePath = object["storePath"] as? String ?? ""
  let graphDBPath = object["graphDBPath"] as? String ?? ""
  let contextObject = object["context"] as? [String: Any] ?? [:]

  if storePath.isEmpty {
    throw RegisterSessionDecodeError(code: "missing_store_path", message: "storePath is required")
  }

  if graphDBPath.isEmpty {
    throw RegisterSessionDecodeError(code: "missing_graph_db_path", message: "graphDBPath is required")
  }

  guard let context = decodeIngestContext(from: contextObject) else {
    throw RegisterSessionDecodeError(code: "invalid_context", message: "context is required")
  }

  if context.indexStorePath != storePath {
    throw RegisterSessionDecodeError(
      code: "mismatched_store_path",
      message: "context.indexStorePath must match storePath"
    )
  }

  if context.graphDBPath != graphDBPath {
    throw RegisterSessionDecodeError(
      code: "mismatched_graph_db_path",
      message: "context.graphDBPath must match graphDBPath"
    )
  }

  return RegisterSessionParams(
    storePath: storePath,
    graphDBPath: graphDBPath,
    context: context
  )
}

func decodeIngestContext(from object: [String: Any]) -> IngestContext? {
  guard JSONSerialization.isValidJSONObject(object),
        let data = try? JSONSerialization.data(withJSONObject: object) else {
    return nil
  }
  return try? JSONDecoder().decode(IngestContext.self, from: data)
}

private func parseSocketPath() -> String {
  let args = CommandLine.arguments
  if let index = args.firstIndex(of: "--socket"), index + 1 < args.count {
    return args[index + 1]
  }
  return ProcessInfo.processInfo.environment["ORCHARD_INDEXD_SOCKET"]
    ?? "/tmp/orchard-indexd.sock"
}

private func parsePIDFilePath(socketPath: String) -> String? {
  let args = CommandLine.arguments
  if let index = args.firstIndex(of: "--pid-file"), index + 1 < args.count {
    return args[index + 1]
  }
  if let configured = ProcessInfo.processInfo.environment["ORCHARD_INDEXD_PID_FILE"], !configured.isEmpty {
    return configured
  }
  return URL(fileURLWithPath: socketPath).deletingPathExtension().path + ".pid"
}

private func collectRuntimeInfo() -> RuntimeInfo {
  let rawPath = CommandLine.arguments.first ?? ProcessInfo.processInfo.arguments.first ?? ""
  let executablePath = URL(fileURLWithPath: rawPath).resolvingSymlinksInPath().path
  var fileInfo = stat()
  let statResult = executablePath.withCString { pathPtr in
    stat(pathPtr, &fileInfo)
  }
  let size = statResult == 0 ? UInt64(fileInfo.st_size) : 0
  let mtimeNs: UInt64
  if statResult == 0 {
    mtimeNs = UInt64(fileInfo.st_mtimespec.tv_sec) * 1_000_000_000
      + UInt64(fileInfo.st_mtimespec.tv_nsec)
  } else {
    mtimeNs = 0
  }
  return RuntimeInfo(
    pid: getpid(),
    executablePath: executablePath,
    binarySize: size,
    binaryMTimeNs: mtimeNs
  )
}

private func writePIDFile(path: String, pid: Int32) throws {
  let directory = URL(fileURLWithPath: path).deletingLastPathComponent()
  try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  try "\(pid)\n".write(toFile: path, atomically: true, encoding: .utf8)
}

private func writeLine<T: Encodable>(_ response: DaemonResponse<T>, to handle: FileHandle) throws {
  let encoded = try JSONEncoder().encode(response)
  handle.write(encoded)
  handle.write("\n".data(using: .utf8)!)
}

private func writeRaw(_ object: [String: Any], to handle: FileHandle) throws {
  let encoded = try JSONSerialization.data(withJSONObject: object, options: [])
  handle.write(encoded)
  handle.write("\n".data(using: .utf8)!)
}

private extension Array {
  func chunked(into size: Int) -> [[Element]] {
    guard size > 0 else { return [self] }
    return stride(from: 0, to: count, by: size).map { start in
      Array(self[start..<Swift.min(start + size, count)])
    }
  }
}
