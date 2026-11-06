import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum LanguageRoutingProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("TTS matrix failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 7 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR PLAN BASELINE PRELIMINARY") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let texts = plan["texts"] as! [[String: Any]]
        let voices = plan["voices"] as! [String]
        let seeds = plan["seeds"] as! [Int]
        let temperatures = plan["temperatures"] as! [Double]
        let expected = plan["expected_generations"] as! Int
        guard expected == texts.count * voices.count * seeds.count * temperatures.count else {
            throw Failure(message: "Invalid fixed matrix size")
        }
        let baseline = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[5]))) as! [String: Any]
        let reference = baseline["failure_observed"] as! [String: Any]
        let preliminaryData = try Data(contentsOf: URL(fileURLWithPath: args[6]))
        let preliminary = try JSONSerialization.jsonObject(with: preliminaryData) as! [String: Any]
        guard preliminary["status"] as? String == "diagnostic_completed",
              let prelimCases = preliminary["results"] as? [[String: Any]], prelimCases.count == 6,
              prelimCases.filter({ $0["original_failure_parity"] as? Bool == true }).count == 2 else {
            throw Failure(message: "Preliminary baseline parity missing")
        }
        let referenceAudio = URL(fileURLWithPath: args[5]).deletingLastPathComponent()
            .appendingPathComponent(reference["audio_file"] as! String)
        let referencePCMHash = digest(try Data(contentsOf: referenceAudio))
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "scope": plan["purpose"]!, "plan_sha256": digest(planData), "plan": plan,
            "original_failure_report_sha256": digest(try Data(contentsOf: URL(fileURLWithPath: args[5]))),
            "original_failure_pcm_sha256": referencePCMHash, "preliminary_failure_parity_sha256": digest(preliminaryData)]
        var results = [[String: Any]]()
        var totalBytes = 0
        func save() throws {
            report["results"] = results
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
            report["completed_generations"] = results.count
            report["pcm_bytes"] = totalBytes
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                .write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                let configuration = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (configuration["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for text in texts {
                    for voice in voices {
                        for seed in seeds {
                            for temperature in temperatures {
                                let label = "\(text["id"]!)-\(voice)-s\(seed)-sub\(Int((temperature * 10).rounded()))"
                                let textValue = text["text"] as! String
                                let begin = ProcessInfo.processInfo.systemUptime
                                MLXRandom.seed(UInt64(seed))
                                var tokens = [Int](), audio = Data(), invalidAudio = false
                                try model.generateManaged(text: textValue, voice: voice,
                                    language: text["language"] as! String, maxTokens: 1200,
                                    checkCancellation: {
                                        let now = ProcessInfo.processInfo.systemUptime
                                        if now - began > plan["total_budget_seconds"] as! Double ||
                                            now - begin > plan["individual_budget_seconds"] as! Double {
                                            throw Failure(message: "Predeclared diagnostic time bound reached")
                                        }
                                        if invalidAudio { throw Failure(message: "Nonfinite audio") }
                                    }, onToken: { tokens.append($0) },
                                    onAudioChunk: { chunk in
                                        let samples = chunk.asArray(Float.self)
                                        guard samples.allSatisfy(\.isFinite) else { invalidAudio = true; return }
                                        let pcm = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                                        audio.append(pcm.withUnsafeBytes { Data($0) })
                                    })
                                guard !invalidAudio else { throw Failure(message: "Nonfinite audio") }
                                let pcmHash = digest(audio)
                                let tokenData = try JSONSerialization.data(withJSONObject: tokens)
                                try audio.write(to: root.appendingPathComponent(label + ".pcm"), options: .withoutOverwriting)
                                try tokenData.write(to: root.appendingPathComponent(label + "-tokens.json"), options: .withoutOverwriting)
                                totalBytes += audio.count
                                let result: [String: Any] = ["id": label, "text_id": text["id"]!, "text": textValue,
                                    "voice": voice, "language": text["language"]!, "seed": seed, "temperature": 0.7, "subtalker_temperature": temperature,
                                    "eos_seen": tokens.last == eos, "token_count": tokens.count, "tokens_sha256": digest(tokenData),
                                    "pcm_sha256": pcmHash, "audio_seconds": Double(audio.count) / 48000,
                                    "processing_seconds": ProcessInfo.processInfo.systemUptime - begin]
                                results.append(result)
                                Memory.clearCache()
                                try save()
                                print("CASE", results.count, "of", expected, label, "EOS", result["eos_seen"]!, "audio", result["audio_seconds"]!)
                                guard totalBytes <= plan["maximum_disk_bytes"] as! Int else {
                                    throw Failure(message: "Predeclared disk bound reached")
                                }
                            }
                        }
                    }
                }
            }
            guard results.count == expected else { throw Failure(message: "Matrix incomplete") }
            report["status"] = "diagnostic_completed"
        } catch {
            report["status"] = "failed"; report["error"] = String(describing: error)
            report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
            throw error
        }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
