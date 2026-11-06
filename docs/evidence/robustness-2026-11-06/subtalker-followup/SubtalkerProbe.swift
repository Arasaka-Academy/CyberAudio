import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum SubtalkerProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Subtalker diagnostic failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 6 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR PLAN BASELINE") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let cases = plan["cases"] as! [[String: Any]]
        guard cases.count == plan["expected_generations"] as! Int else { throw Failure(message: "Plan count mismatch") }
        let baselineData = try Data(contentsOf: URL(fileURLWithPath: args[5]))
        let baseline = try JSONSerialization.jsonObject(with: baselineData) as! [String: Any]
        let failure = baseline["failure_observed"] as! [String: Any]
        let referenceTokens = failure["tokens"] as! [Int]
        let referencePCMHash = digest(try Data(contentsOf: URL(fileURLWithPath: args[5]).deletingLastPathComponent()
            .appendingPathComponent(failure["audio_file"] as! String)))
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "plan": plan, "plan_sha256": digest(planData), "original_failure_report_sha256": digest(baselineData),
            "original_failure_pcm_sha256": referencePCMHash]
        var results = [[String: Any]](), totalBytes = 0
        func save() throws {
            report["results"] = results; report["completed_generations"] = results.count
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
            report["pcm_bytes"] = totalBytes
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                .write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1])
                    .appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for row in cases {
                    let label = row["id"] as! String, seed = row["seed"] as! Int
                    let temperature = row["subtalker_temperature"] as! Double
                    MLXRandom.seed(UInt64(seed))
                    let begin = ProcessInfo.processInfo.systemUptime
                    var tokens = [Int](), audio = Data(), invalidAudio = false
                    try model.diagnosticSubtalkerManaged(text: plan["text"] as! String, voice: plan["voice"] as! String,
                        language: plan["language"] as! String, subtalkerTemperature: Float(temperature),
                        checkCancellation: {
                            let now = ProcessInfo.processInfo.systemUptime
                            if now - began > plan["total_budget_seconds"] as! Double ||
                                now - begin > plan["individual_budget_seconds"] as! Double {
                                throw Failure(message: "Predeclared time bound reached")
                            }
                            if invalidAudio { throw Failure(message: "Nonfinite audio") }
                        }, onToken: { tokens.append($0) }, onAudioChunk: { chunk in
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
                    var result: [String: Any] = ["id": label, "seed": seed, "subtalker_temperature": temperature,
                        "eos_seen": tokens.last == eos, "token_count": tokens.count,
                        "tokens_sha256": digest(tokenData), "pcm_sha256": pcmHash,
                        "audio_seconds": Double(audio.count) / 48000,
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - begin]
                    totalBytes += audio.count
                    if seed == 196 && temperature == 0.7 {
                        result["original_failure_parity"] = tokens == referenceTokens && pcmHash == referencePCMHash
                    }
                    results.append(result); try save()
                    if let parity = result["original_failure_parity"] as? Bool, !parity {
                        throw Failure(message: "Original failure parity mismatch")
                    }
                    guard totalBytes <= plan["maximum_disk_bytes"] as! Int else {
                        throw Failure(message: "Predeclared disk bound reached")
                    }
                    Memory.clearCache()
                    print("CASE", results.count, "of", cases.count, label, "EOS", result["eos_seen"]!, "audio", result["audio_seconds"]!)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch {
            report["status"] = "failed"; report["error"] = String(describing: error)
            report["finished"] = ISO8601DateFormatter().string(from: Date()); try save(); throw error
        }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
