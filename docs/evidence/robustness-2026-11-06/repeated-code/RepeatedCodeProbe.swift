import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum RepeatedCodeProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Order candidate failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 6 else { throw Failure(message: "TTS_DIR METALLIB NEW_ROOT PLAN BASELINE_REPORT") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output already exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let baselineURL = URL(fileURLWithPath: args[5])
        let baselineData = try Data(contentsOf: baselineURL)
        let baseline = try JSONSerialization.jsonObject(with: baselineData) as! [String: Any]
        let prior = (baseline["results"] as! [[String: Any]]).filter { $0["mode"] as? String == "prefill" }
        var references = [[String: Any]]()
        for entry in prior {
            let pcm = try Data(contentsOf: baselineURL.deletingLastPathComponent().appendingPathComponent((entry["id"] as! String) + ".pcm"))
            guard digest(pcm) == entry["pcm_sha256"] as! String else { throw Failure(message: "Baseline PCM changed") }
            references.append(entry)
        }
        guard references.count == 5 else { throw Failure(message: "Wrong baseline cohort") }
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let start = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "plan_sha256": digest(planData), "plan": plan, "baseline_report_sha256": digest(baselineData), "baseline_results": references]
        var results = [[String: Any]]()
        func save() throws {
            report["results"] = results; report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - start
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for item in plan["cases"] as! [[String: Any]] {
                    let id = item["id"] as! String
                    MLXRandom.seed(UInt64(item["seed"] as! Int))
                    let began = ProcessInfo.processInfo.systemUptime
                    var tokens = [Int](), pcm = Data(), invalid = false
                    try model.generateManaged(text: item["text"] as! String, voice: item["voice"] as! String,
                        language: item["language"] as! String, maxTokens: plan["max_tokens"] as! Int,
                        checkCancellation: {
                            let now = ProcessInfo.processInfo.systemUptime
                            guard !invalid, now - start < plan["total_budget_seconds"] as! Double,
                                  now - began < plan["individual_budget_seconds"] as! Double else { throw Failure(message: "Invalid audio or diagnostic budget") }
                        }, onToken: { tokens.append($0) }, onAudioChunk: { chunk in
                            let samples = chunk.asArray(Float.self)
                            guard samples.allSatisfy(\.isFinite) else { invalid = true; return }
                            let values = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                            pcm.append(values.withUnsafeBytes { Data($0) })
                        })
                    guard !invalid, !tokens.isEmpty, !pcm.isEmpty else { throw Failure(message: "Invalid generation") }
                    try pcm.write(to: root.appendingPathComponent(id + ".pcm"), options: .withoutOverwriting)
                    let result: [String: Any] = ["id": id, "case": item, "tokens": tokens, "token_count": tokens.count,
                        "eos_seen": tokens.last == eos, "pcm_file": id + ".pcm", "pcm_sha256": digest(pcm),
                        "pcm_bytes": pcm.count, "audio_seconds": Double(pcm.count)/48000,
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - began]
                    results.append(result); Memory.clearCache(); try save()
                    print("GENERATION", id, "EOS", tokens.last == eos, "seconds", Double(pcm.count)/48000)
                }
            }
            let failure = results.first { $0["id"] as? String == "known-failure" }!
            let repeated = results.first { $0["id"] as? String == "known-failure-repeat" }!
            report["failure_repeat_tokens_exact"] = (failure["tokens"] as! [Int]) == (repeated["tokens"] as! [Int])
            report["failure_repeat_pcm_exact"] = failure["pcm_sha256"] as! String == repeated["pcm_sha256"] as! String
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
