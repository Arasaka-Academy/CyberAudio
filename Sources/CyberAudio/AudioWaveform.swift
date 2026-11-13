import Foundation

struct AudioWaveform: Sendable {
    let samples: [Float]
    let duration: Double
    static let empty = AudioWaveform(samples: [], duration: 0)

    /// TTS returns PCM16 WAV. Read chunks rather than assuming a 44-byte header;
    /// metadata and stereo fixtures must not become visible audio samples.
    static func decodeWAV(_ data: Data) throws -> AudioWaveform {
        try Task.checkCancellation()
        return try data.withUnsafeBytes { raw in
            func u16(_ offset: Int) -> UInt16 { UInt16(littleEndian: raw.loadUnaligned(fromByteOffset: offset, as: UInt16.self)) }
            func u32(_ offset: Int) -> UInt32 { UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: offset, as: UInt32.self)) }
            guard raw.count >= 12, u32(0) == 0x46464952, u32(8) == 0x45564157 else { return .empty }
            let end = Int(u32(4)) + 8
            guard end <= raw.count, end >= 12 else { return .empty }
            var channels = 0
            var sampleRate = 0
            var audioRange: Range<Int>?
            var offset = 12
            while offset <= end - 8 {
                try Task.checkCancellation()
                let size = Int(u32(offset + 4))
                let start = offset + 8
                guard size <= end - start else { return .empty }
                switch u32(offset) {
                case 0x20746d66: // fmt
                    guard size >= 16, u16(start) == 1, u16(start + 14) == 16 else { return .empty }
                    channels = Int(u16(start + 2))
                    sampleRate = Int(u32(start + 4))
                    guard (1...8).contains(channels), sampleRate > 0,
                          Int(u16(start + 12)) == channels * 2 else { return .empty }
                case 0x61746164: // data
                    audioRange = start..<(start + size)
                default: break
                }
                offset = start + size + size % 2
            }
            guard channels > 0, let range = audioRange, range.count % (channels * 2) == 0 else { return .empty }
            var accumulator = WaveformAccumulator(sampleRate: Double(sampleRate))
            var frame = 0
            for index in stride(from: range.lowerBound, to: range.upperBound, by: channels * 2) {
                if frame % 4096 == 0 { try Task.checkCancellation() }
                var low: Float = 0
                var high: Float = 0
                for channel in 0..<channels {
                    let value = Float(Int16(bitPattern: u16(index + channel * 2))) / 32768
                    low = min(low, value); high = max(high, value)
                }
                accumulator.append(low: low, high: high)
                frame += 1
            }
            return accumulator.waveform
        }
    }
}

/// A peak-preserving overview with at most 1,026 display samples. As a stream
/// grows, adjacent bins merge; neither the full audio nor unbounded history is
/// retained here. Appending a chunk only scans that new chunk.
struct WaveformAccumulator {
    private let sampleRate: Double
    private var bins: [Float] = []
    private var binWidth = 256
    private var binFrames = 0
    private var frameCount = 0
    private var low: Float = 0
    private var high: Float = 0

    init(sampleRate: Double = 24000) { self.sampleRate = sampleRate }

    mutating func appendPCM16(_ data: Data) {
        guard data.count % 2 == 0 else { return }
        data.withUnsafeBytes { raw in
            for index in stride(from: 0, to: raw.count, by: 2) {
                let value = Float(Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index, as: Int16.self))) / 32768
                append(low: value, high: value)
            }
        }
    }

    mutating func append(low: Float, high: Float) {
        self.low = min(self.low, low)
        self.high = max(self.high, high)
        binFrames += 1
        frameCount += 1
        if binFrames == binWidth {
            bins.append(self.low); bins.append(self.high)
            binFrames = 0; self.low = 0; self.high = 0
            if bins.count == 1024 {
                bins = stride(from: 0, to: bins.count, by: 4).flatMap {
                    [min(bins[$0], bins[$0 + 2]), max(bins[$0 + 1], bins[$0 + 3])]
                }
                binWidth *= 2
            }
        }
    }

    var waveform: AudioWaveform {
        let tail: [Float] = binFrames > 0 ? [low, high] : []
        return AudioWaveform(samples: bins + tail, duration: Double(frameCount) / sampleRate)
    }
}
