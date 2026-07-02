import Foundation

struct DaemonError: Encodable {
  let code: String
  let message: String
}

struct DaemonResponse<Result: Encodable>: Encodable {
  let id: String
  let ok: Bool
  let result: Result?
  let error: DaemonError?
}

struct WarmParams {
  let storePath: String
  let sourceRoots: [String]
  let targets: [String]
  let dylibPath: String?
}

struct WarmResult: Encodable {
  let sessionId: String
  let reused: Bool
  let dbPath: String
}

struct PingResult: Encodable {
  let status: String
  let protocolVersion: Int
  let pid: Int32
  let executablePath: String
  let binarySize: UInt64
  let binaryMTimeNs: UInt64
}

struct ScanParams {
  let sessionId: String
  let incrementalSince: Double?
  let emitOccurrences: Bool
}

struct ScanSummary: Encodable {
  let symbols: Int
  let relations: Int
  let changedFiles: Int
  let allFiles: Int
}
