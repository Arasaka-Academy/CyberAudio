// Reproducible, narrowly scoped adapter for the fixed upstream SDK revision.
import Foundation
import CryptoKit

guard CommandLine.arguments.count == 2 else { fatalError("Usage: swift patch_mlx_audio.swift <mlx-audio-swift checkout>") }
let checkout = URL(fileURLWithPath:CommandLine.arguments[1])
let source = checkout.appendingPathComponent("Sources/MLXAudioTTS/Models/Qwen3TTS/Qwen3TTS.swift")
var text = try String(contentsOf:source,encoding:.utf8)
let destructiveCacheRecovery = """
            // speech_tokenizer exists but is not a directory — stale cache
            // Remove the entire model cache so it re-downloads cleanly next time
            print("speech_tokenizer is not a directory (stale cache), clearing model cache...")
            try? fm.removeItem(at: modelDir)
            throw AudioGenerationError.modelNotInitialized(
                "Model cache was corrupted (speech_tokenizer). It has been cleared. Please try loading again."
            )
"""
let preservingCacheFailure = """
            // Configured model directories may contain user-owned files.
            throw AudioGenerationError.modelNotInitialized(
                "speech_tokenizer is not a directory. Model files were preserved; check the configured model directory."
            )
"""
let adapter = """

// CyberAudio managed generation: the caller owns the producer and its completion.
extension Qwen3TTSModel {
    public func generateManaged(
        text: String, voice: String, language: String, maxTokens: Int,
        checkCancellation: @escaping () throws -> Void,
        onToken: @escaping (Int) -> Void,
        onAudioChunk: @escaping (MaterializedArray) -> Void
    ) throws {
        try requireGenerationComponents()
        _ = try generateVoiceDesign(text: text, instruct: voice, language: language,
            refAudio: nil, refText: nil, temperature: 0.7, topK: 50, topP: 1,
            repetitionPenalty: 1.05, minP: 0, maxTokens: maxTokens,
            streamingInterval: 0.32, onToken: onToken, onAudioChunk: onAudioChunk,
            checkCancellation: checkCancellation)
    }
}

"""
func modify(_ original: String) throws -> String {
    var result = original
    func replace(_ before: String,_ after: String,count: Int = 1) throws {
        guard result.components(separatedBy:before).count-1 == count else {
            throw NSError(domain:"Unexpected MLX Audio source; refusing to patch",code:1)
        }
        result = result.replacingOccurrences(of:before,with:after)
    }
    try replace("onAudioChunk: ((MaterializedArray) -> Void)? = nil\n    ) throws -> MLXArray", "onAudioChunk: ((MaterializedArray) -> Void)? = nil,\n        checkCancellation: (() throws -> Void)? = nil\n    ) throws -> MLXArray")
    try replace("let effectiveMaxTokens = min(maxTokens, max(75, targetTokenCount * 6))", "let effectiveMaxTokens = checkCancellation == nil ? min(maxTokens, max(75, targetTokenCount * 6)) : maxTokens")
    // A dialect speaker may specialize Chinese/auto, but must honor explicit English.
    try replace("if let dialectMap = talkerConfig.spkIsDialect,", "if [\"chinese\", \"auto\"].contains(language.lowercased()),\n               let dialectMap = talkerConfig.spkIsDialect,")
    try replace(destructiveCacheRecovery, preservingCacheFailure)
    let start = result.range(of:"    func generateVoiceDesign(")!.lowerBound
    let end = result.range(of:"    static func parseCustomVoicePrompt",range:start..<result.endIndex)!.lowerBound
    let section = String(result[start..<end])
    guard section.components(separatedBy:"try Task.checkCancellation()").count-1 == 3 else {
        throw NSError(domain:"Unexpected cancellation boundaries",code:1)
    }
    result.replaceSubrange(start..<end,with:section.replacingOccurrences(of:"try Task.checkCancellation()",with:"try checkCancellation?(); try Task.checkCancellation()"))
    return result + adapter
}
if text.hasSuffix(adapter) {
    // Reconstruct the original, so an already-patched checkout is verified too.
    var original = String(text.dropLast(adapter.count))
    original = original.replacingOccurrences(of:"onAudioChunk: ((MaterializedArray) -> Void)? = nil,\n        checkCancellation: (() throws -> Void)? = nil",with:"onAudioChunk: ((MaterializedArray) -> Void)? = nil")
    original = original.replacingOccurrences(of:"let effectiveMaxTokens = checkCancellation == nil ? min(maxTokens, max(75, targetTokenCount * 6)) : maxTokens",with:"let effectiveMaxTokens = min(maxTokens, max(75, targetTokenCount * 6))")
    original = original.replacingOccurrences(of:"if [\"chinese\", \"auto\"].contains(language.lowercased()),\n               let dialectMap = talkerConfig.spkIsDialect,",with:"if let dialectMap = talkerConfig.spkIsDialect,")
    original = original.replacingOccurrences(of:preservingCacheFailure,with:destructiveCacheRecovery)
    original = original.replacingOccurrences(of:"try checkCancellation?(); try Task.checkCancellation()",with:"try Task.checkCancellation()")
    let hash = SHA256.hash(data:Data(original.utf8)).map { String(format:"%02x",$0) }.joined()
    let prepared = try modify(original)
    let previous = SHA256.hash(data:Data(text.utf8)).map { String(format:"%02x",$0) }.joined()
    guard hash == "6d4d6efc3f19437a512dfe2446242d16cfedbe190d3a7ac39caa673442de6f7a",
          prepared == text || ["0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426",
                               "0190fd2a02d6ce0eaa3732ca7b7d373be251747edc5d14a0d0b4fe8ce3c0dd6b"].contains(previous) else { fatalError("Modified SDK does not match the pinned patch") }
    if prepared != text {
        text = prepared
        try text.write(to:source,atomically:true,encoding:.utf8)
    }
} else {
    let hash = SHA256.hash(data:Data(text.utf8)).map { String(format:"%02x",$0) }.joined()
    guard hash == "6d4d6efc3f19437a512dfe2446242d16cfedbe190d3a7ac39caa673442de6f7a" else { fatalError("SDK source revision mismatch") }
    text = try modify(text)
    try text.write(to:source,atomically:true,encoding:.utf8)
}
// Preserve the upstream nonthrowing ASR entry point. The managed entry point
// shares its implementation, with cancellation while retaining the producer.
let asrSource = checkout.appendingPathComponent("Sources/MLXAudioSTT/Models/Qwen3ASR/Qwen3ASR.swift")
let asrHash = "71f5cbc43ab0591bee0c10f843d216e03596e55dad458c62cf9b5040e3c6d883"
let asrMarker = "\n// CyberAudio cancellable ASR adapter.\n"
let asrSignature = """
    public func generate(
        audio: MLXArray,
        maxTokens: Int = 8192,
        temperature: Float = 0.0,
        context: String = "",
        language: String? = nil,
        chunkDuration: Float = 1200.0,
        minChunkDuration: Float = 1.0,
        repetitionPenalty: Float = 1.0,
        repetitionContextSize: Int = 32
    ) -> STTOutput {
"""
let managedASRSignature = asrSignature.replacingOccurrences(of: "public func generate(", with: "public func generateManaged(")
    .replacingOccurrences(of: "repetitionContextSize: Int = 32\n    ) ->", with: "repetitionContextSize: Int = 32,\n        checkCancellation: () throws -> Void\n    ) rethrows ->")
let asrWrapper = asrSignature + """

        generateManaged(audio: audio, maxTokens: maxTokens, temperature: temperature,
            context: context, language: language, chunkDuration: chunkDuration,
            minChunkDuration: minChunkDuration, repetitionPenalty: repetitionPenalty,
            repetitionContextSize: repetitionContextSize, checkCancellation: {})
    }

"""
let asrChanges: [(String,String)] = [
    (asrSignature, asrWrapper + managedASRSignature + "\n        try checkCancellation()"),
    ("repetitionContextSize: Int = 32\n    ) -> (text: String, language: String?, promptTokens: Int, generationTokens: Int) {",
     "repetitionContextSize: Int = 32,\n        checkCancellation: () throws -> Void\n    ) rethrows -> (text: String, language: String?, promptTokens: Int, generationTokens: Int) {\n        try checkCancellation()"),
    ("        let (inputFeatures, featureAttentionMask, numAudioTokens) = preprocessAudio(audio)",
     "        let (inputFeatures, featureAttentionMask, numAudioTokens) = preprocessAudio(audio)\n        try checkCancellation()"),
    ("        eval(audioFeatures)\n\n        let embeds =", "        eval(audioFeatures)\n        try checkCancellation()\n\n        let embeds ="),
    ("        while totalTokens - processedTokens > 1 {", "        while totalTokens - processedTokens > 1 {\n            try checkCancellation()"),
    ("        var prevTokenArr = firstLast.argMax(axis: -1)\n        asyncEval(prevTokenArr)",
     "        var prevTokenArr = firstLast.argMax(axis: -1)\n        defer { eval(prevTokenArr) } // Join any pipelined GPU work before returning.\n        asyncEval(prevTokenArr)"),
    ("            let prevTokenInt = prevTokenArr.item(Int.self)", "            let prevTokenInt = prevTokenArr.item(Int.self)\n            try checkCancellation()"),
    ("        for (chunkAudio, offsetSec) in chunks {", "        for (chunkAudio, offsetSec) in chunks {\n            try checkCancellation()"),
    ("            let result = generateSingleChunk(", "            let result = try generateSingleChunk("),
    ("                repetitionContextSize: repetitionContextSize\n            )",
     "                repetitionContextSize: repetitionContextSize,\n                checkCancellation: checkCancellation\n            )")
]
func changeASR(_ input: String, reverse: Bool = false) throws -> String {
    var result = input
    let changes = reverse ? Array(asrChanges.reversed()).map { ($0.1,$0.0) } : asrChanges
    for (before,after) in changes {
        guard result.components(separatedBy:before).count-1 == 1 else {
            throw NSError(domain:"Unexpected ASR source; refusing to patch",code:1)
        }
        result = result.replacingOccurrences(of:before,with:after)
    }
    return result
}
let asrText = try String(contentsOf:asrSource,encoding:.utf8)
let alreadyPatched = asrText.hasSuffix(asrMarker)
let originalASR = alreadyPatched ? try changeASR(String(asrText.dropLast(asrMarker.count)),reverse:true) : asrText
guard SHA256.hash(data:Data(originalASR.utf8)).map({ String(format:"%02x",$0) }).joined() == asrHash else {
    fatalError("ASR source revision mismatch")
}
let patchedASR = try changeASR(originalASR) + asrMarker
if alreadyPatched {
    guard patchedASR == asrText else { fatalError("Modified ASR does not match the pinned patch") }
} else {
    try patchedASR.write(to:asrSource,atomically:true,encoding:.utf8)
}
print("Verified CyberAudio managed Qwen TTS and cancellable ASR adapters.")
