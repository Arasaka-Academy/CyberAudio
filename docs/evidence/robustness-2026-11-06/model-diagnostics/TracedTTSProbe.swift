import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum TracedTTSProbe {
    struct Failure: Error { let message: String }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Traced TTS diagnostic failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR BASELINE_REPORT") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let baseline = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[4]))) as! [String: Any]
        let reference = baseline["failure_observed"] as! [String: Any]
        let referenceTokens = reference["tokens"] as! [Int]
        let started = ProcessInfo.processInfo.systemUptime
        try await Device.withDefaultDevice(.gpu) {
            let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
            let configuration = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
            let eos = (configuration["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
            var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
                "scope": "Private SDK sampler trace; baseline parity required before interpretation; no production parameter change",
                "text": "持续验证语音服务的稳定性和恢复能力。", "voice": "ono_anna", "eos_token": eos]
            var results = [[String: Any]]()
            func save() throws {
                report["results"] = results
                report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime-started
                try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
            }
            func generate(_ label: String, seed: UInt64, temperature: Float, trace: Bool) throws -> [String: Any] {
                MLXRandom.seed(seed)
                var tokens = [Int](), samples = [[String: Any]](), audio = Data()
                let begin = ProcessInfo.processInfo.systemUptime
                try model.diagnosticManaged(text: "持续验证语音服务的稳定性和恢复能力。", voice: "ono_anna", language: "chinese", temperature: temperature,
                    checkCancellation: {
                        if ProcessInfo.processInfo.systemUptime-started > 900 { throw Failure(message: "Diagnostic budget reached") }
                    }, onToken: { token in
                        tokens.append(token)
                        if trace && !samples.isEmpty { samples[samples.count-1]["sampled_token"] = token }
                    }, onSample: { raw, filtered in
                        guard trace else { return }
                        let before = raw.asArray(Float.self), after = filtered.asArray(Float.self)
                        let maximum = after.max()!
                        let denominator = after.reduce(0.0) { $0 + exp(Double(($1-maximum)/temperature)) }
                        let probability = exp(Double((after[eos]-maximum)/temperature))/denominator
                        let entry: [String: Any] = ["step": samples.count, "eos_rank_before_top_k": before.filter { $0 > before[eos] }.count+1,
                            "eos_logit_before_top_k": before[eos].isFinite ? before[eos] as Any : NSNull(),
                            "eos_logit_after_filters": after[eos].isFinite ? after[eos] as Any : NSNull(),
                            "eos_probability": probability.isFinite ? probability as Any : NSNull(),
                            "filtered_tokens": after.filter { !$0.isFinite }.count]
                        samples.append(entry)
                    }, onAudioChunk: { chunk in
                        let pcm = chunk.asArray(Float.self).map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                        audio.append(pcm.withUnsafeBytes { Data($0) })
                    })
                try audio.write(to: root.appendingPathComponent(label+".pcm"), options: .withoutOverwriting)
                let sampleData = try JSONSerialization.data(withJSONObject: samples, options: [.prettyPrinted, .sortedKeys])
                try sampleData.write(to: root.appendingPathComponent(label+"-sampler.json"), options: .withoutOverwriting)
                Memory.clearCache()
                let result: [String: Any] = ["label": label, "seed": seed, "temperature": temperature,
                    "trace": trace, "tokens": tokens, "token_count": tokens.count, "eos_seen": tokens.last == eos,
                    "audio_seconds": Double(audio.count)/48000, "pcm_sha256": SHA256.hash(data: audio).map { String(format: "%02x", $0) }.joined(),
                    "processing_seconds": ProcessInfo.processInfo.systemUptime-begin,
                    "matches_original_failure_tokens": tokens == referenceTokens, "sampler_steps": samples.count]
                print(label, "EOS", result["eos_seen"]!, "seconds", result["audio_seconds"]!)
                return result
            }
            do {
                results.append(try generate("baseline-untraced", seed: 196, temperature: 0.7, trace: false)); try save()
                results.append(try generate("baseline-traced", seed: 196, temperature: 0.7, trace: true)); try save()
                results.append(try generate("temperature-09", seed: 196, temperature: 0.9, trace: true)); try save()
                results.append(try generate("temperature-05", seed: 196, temperature: 0.5, trace: true)); try save()
                results.append(try generate("recovery-seed-31", seed: 31, temperature: 0.7, trace: true)); try save()
                report["status"] = "diagnostic_completed"
            } catch {
                report["status"] = "failed"; report["error"] = String(describing: error)
                try save(); throw error
            }
            report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
        }
    }
}
