import Foundation

struct BufferedLineWriter {
  private let flushThresholdBytes: Int
  private let sink: (Data) -> Void
  private var buffer = Data()

  init(flushThresholdBytes: Int = 262_144, sink: @escaping (Data) -> Void) {
    self.flushThresholdBytes = flushThresholdBytes
    self.sink = sink
  }

  mutating func writeLine(_ line: String) {
    buffer.append(contentsOf: line.utf8)
    buffer.append(0x0A)
    if buffer.count >= flushThresholdBytes {
      flush()
    }
  }

  mutating func flush() {
    guard !buffer.isEmpty else {
      return
    }
    sink(buffer)
    buffer.removeAll(keepingCapacity: true)
  }
}
