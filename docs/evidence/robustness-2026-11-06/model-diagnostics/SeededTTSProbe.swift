import Foundation
import Darwin
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum SeededTTSProbe {
    struct Failure: Error { let message: String }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Seeded TTS diagnostic failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 4 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let started = ProcessInfo.processInfo.systemUptime
        try await Device.withDefaultDevice(.gpu) {
            let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
            let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
            let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
            var results = [[String: Any]]()
            var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
                "scope": "Direct unchanged managed TTS adapter, fixed seeds; no HTTP/App or listening acceptance",
                "voice": "ono_anna", "text": "持续验证语音服务的稳定性和恢复能力。", "language": "chinese",
                "temperature": 0.7, "top_k": 50, "top_p": 1, "repetition_penalty": 1.05, "max_tokens": 1200,
                "maximum_seeds": 256, "maximum_seconds": 1800]
            func save() throws {
                report["results"] = results
                report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - started
                try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
            }
            func generate(seed: UInt64, label: String) throws -> [String: Any] {
                MLXRandom.seed(seed)
                var tokens = [Int](), audio = Data()
                let begin = ProcessInfo.processInfo.systemUptime
                try model.generateManaged(text: "持续验证语音服务的稳定性和恢复能力。", voice: "ono_anna", language: "chinese", maxTokens: 1200,
                    checkCancellation: {
                        guard ProcessInfo.processInfo.systemUptime - started < 1800 else { throw Failure(message: "Diagnostic time budget reached") }
                    }, onToken: { tokens.append($0) }, onAudioChunk: { chunk in
                        let samples = chunk.asArray(Float.self)
                        let pcm = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                        audio.append(pcm.withUnsafeBytes { Data($0) })
                    })
                let path = root.appendingPathComponent(label + ".pcm")
                try audio.write(to: path, options: .withoutOverwriting)
                defer { Memory.clearCache() }
                return ["seed": seed, "label": label, "eos_seen": tokens.last == eos,
                    "token_count": tokens.count, "tokens": tokens, "audio_file": path.lastPathComponent,
                    "pcm_bytes": audio.count, "audio_seconds": Double(audio.count)/48000,
                    "processing_seconds": ProcessInfo.processInfo.systemUptime - begin,
                    "metal_active_bytes": Memory.activeMemory, "metal_peak_bytes": Memory.peakMemory]
            }
            do {
                var failedSeed: UInt64?
                for seed in UInt64(0)..<256 {
                    let result = try generate(seed: seed, label: "seed-\(seed)")
                    results.append(result)
                    try save()
                    print("SEED \(seed) EOS \(result["eos_seen"]!) seconds \(result["audio_seconds"]!)")
                    if result["eos_seen"] as? Bool != true {
                        failedSeed = seed; report["failure_observed"] = result; break
                    }
                }
                if let failedSeed {
                    let repeated = try generate(seed: failedSeed, label: "failure-repeat")
                    results.append(repeated)
                    report["same_seed_repeat"] = repeated
                    report["tokens_repeat_identically"] = (repeated["tokens"] as! [Int]) == (report["failure_observed"] as! [String: Any])["tokens"] as! [Int]
                    let recovered = try generate(seed: 0, label: "recovery-seed-0")
                    results.append(recovered); report["same_model_recovery"] = recovered
                    report["observation"] = "no_eos_at_token_limit"
                } else { report["observation"] = "not_reproduced_within_256_seeds" }
                report["status"] = "diagnostic_completed"
            } catch {
                report["status"] = "failed"; report["error"] = String(describing: error)
                try save(); throw error
            }
            report["finished"] = ISO8601DateFormatter().string(from: Date())
            try save()
        }
    }
}
