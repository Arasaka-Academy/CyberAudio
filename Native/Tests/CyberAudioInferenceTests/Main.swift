import Foundation
import CyberAudioCore
import CyberAudioInference
import MLX
import MLXAudioCore
import MLXAudioSTT

/// Opt-in real ASR adapter acceptance. Requires explicit local assets and audio;
/// no downloads, microphone, production service, or production configuration.
@main enum InferenceTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        guard condition() else { throw Failure(message: message) }
    }
    static func main() async {
        do { try await run() }
        catch { fputs("ASR adapter acceptance failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        if args.count == 4,args[1] == "--load" {
            let root = URL(fileURLWithPath:args[2])
            let settings = try ServiceSettings(root:root,configuration:AssetStore.configuration(root:root),
                                               token:String(repeating:"x",count:64),instance:"isolated-model-load")
            let engine = try await QwenEngine.load(settings:settings,metallib:URL(fileURLWithPath:args[3]))
            print("Real ASR/TTS load and warmup passed: \(engine.metrics)")
            return
        }
        guard args.count == 4 else {
            throw Failure(message: "Usage: CyberAudioInferenceTests ASR_DIRECTORY METALLIB AUDIO_FILE")
        }
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512*1024*1024
        let asr = try await Qwen3ASRModel.fromModelDirectory(URL(fileURLWithPath: args[1]))
        let (_,audio) = try loadAudioArray(from: URL(fileURLWithPath: args[3]), sampleRate: 16000)
        defer { Memory.clearCache() }

        let cancelled = CancellationToken(timeout: 120)
        cancelled.cancel()
        do {
            _ = try asr.generateManaged(audio: audio, maxTokens: 128, temperature: 0, language: "Chinese",
                                        checkCancellation: { try cancelled.check() })
            throw Failure(message: "Pre-cancelled ASR entered generation")
        } catch let error as ServiceError {
            try require(error.code == "cancelled", "Wrong pre-cancellation failure")
        }

        var baselineChecks = 0
        let baseline = asr.generateManaged(audio: audio, maxTokens: 128, temperature: 0, language: "Chinese",
                                           checkCancellation: { baselineChecks += 1 })
        try require(baseline.generationTokens > 3 && baselineChecks > baseline.generationTokens,
                    "Acceptance audio must exercise multiple decode tokens and encoder checks")
        let stopAt = baselineChecks - 2
        var checks = 0
        let started = uptime()
        var cancelledAt = started
        do {
            _ = try asr.generateManaged(audio: audio, maxTokens: 128, temperature: 0, language: "Chinese",
                                        checkCancellation: {
                checks += 1
                if checks == stopAt { cancelledAt = uptime(); throw ServiceError.cancelled }
            })
            throw Failure(message: "ASR ignored a cancellation inside generation")
        } catch let error as ServiceError {
            try require(error.code == "cancelled" && checks == stopAt, "ASR continued after cancellation")
        }
        let generationMS = (cancelledAt-started)*1000
        let unwindMS = (uptime()-cancelledAt)*1000
        let recovered = asr.generateManaged(audio: audio, maxTokens: 128, temperature: 0, language: "Chinese",
                                            checkCancellation: {})
        try require(!recovered.text.isEmpty && recovered.generationTokens > 0,
                    "The same ASR model must recover after mid-generation cancellation")
        print("ASR adapter: pre-cancel, mid-generation cancel (\(checks)/\(baselineChecks) checks; \(Int(generationMS)) ms before cancellation, \(Int(unwindMS)) ms to return), and same-model recovery passed")
        print("Real local model on supplied audio; this does not prove microphone, corpus accuracy, or human listening.")
    }
}
