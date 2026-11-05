// Reproducible, narrowly scoped adapter for the fixed upstream SDK revision.
import Foundation
import CryptoKit

guard CommandLine.arguments.count == 2 else { fatalError("Usage: swift patch_mlx_audio.swift <mlx-audio-swift checkout>") }
let checkout = URL(fileURLWithPath:CommandLine.arguments[1])
let source = checkout.appendingPathComponent("Sources/MLXAudioTTS/Models/Qwen3TTS/Qwen3TTS.swift")
var text = try String(contentsOf:source,encoding:.utf8)
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
    original = original.replacingOccurrences(of:"try checkCancellation?(); try Task.checkCancellation()",with:"try Task.checkCancellation()")
    let hash = SHA256.hash(data:Data(original.utf8)).map { String(format:"%02x",$0) }.joined()
    guard hash == "6d4d6efc3f19437a512dfe2446242d16cfedbe190d3a7ac39caa673442de6f7a", try modify(original) == text else { fatalError("Modified SDK does not match the pinned patch") }
} else {
    let hash = SHA256.hash(data:Data(text.utf8)).map { String(format:"%02x",$0) }.joined()
    guard hash == "6d4d6efc3f19437a512dfe2446242d16cfedbe190d3a7ac39caa673442de6f7a" else { fatalError("SDK source revision mismatch") }
    text = try modify(text)
    try text.write(to:source,atomically:true,encoding:.utf8)
}
print("Verified CyberAudio managed Qwen generation adapter.")
