
// Private diagnostic access only; production generation and decoder bodies stay intact.
extension Qwen3TTSModel {
    public var diagnosticDecoderWindow: Int? { speechTokenizer?.decoder.config.slidingWindow }

    public func diagnosticDecodeCodes(
        _ input: [[Int]], checkCancellation: () throws -> Void,
        onAudioChunk: (MaterializedArray) -> Void
    ) throws -> Int {
        guard let decoder = speechTokenizer?.decoder, let talkerConfig = config.talkerConfig else {
            throw AudioGenerationError.modelNotInitialized("Diagnostic decoder missing")
        }
        var codes = input
        guard !codes.isEmpty, codes.count <= 1200, codes.allSatisfy({ $0.count == 16 }) else {
            throw AudioGenerationError.invalidInput("Invalid diagnostic codec shape")
        }
        if codes.last?.first == talkerConfig.codecEosTokenId { codes.removeLast() }
        guard !codes.isEmpty, codes.allSatisfy({ $0.first != talkerConfig.codecEosTokenId }) else {
            throw AudioGenerationError.invalidInput("Invalid diagnostic EOS")
        }
        decoder.resetStreamingState()
        defer { decoder.resetStreamingState() }
        for index in stride(from: 0, to: codes.count, by: 4) {
            try checkCancellation()
            let part = codes[index..<min(index + 4, codes.count)]
            let array = MLXArray(part.flatMap { $0.map { Int32($0) } }).reshaped(1, part.count, 16).transposed(0, 2, 1)
            onAudioChunk(decoder.streamingStep(array))
        }
        return codes.count
    }
}
