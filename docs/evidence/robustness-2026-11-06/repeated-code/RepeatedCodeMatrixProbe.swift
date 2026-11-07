import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum RepeatedCodeMatrixProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Order matrix failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 7, args[6] == "candidate" else { throw Failure(message: "TTS METALLIB NEW_ROOT PLAN PRELIMINARY STAGE") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true), stage = args[6]
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let fresh = Set(plan["fresh_baseline_ids"] as! [String])
        let cases = (plan["cases"] as! [[String: Any]]).filter { stage == "candidate" || fresh.contains($0["id"] as! String) }
        guard cases.count == plan[stage == "candidate" ? "expected_candidates" : "expected_fresh_baselines"] as! Int else { throw Failure(message: "Plan count") }
        let preliminaryData = try Data(contentsOf: URL(fileURLWithPath: args[5]))
        guard digest(preliminaryData) == plan["preliminary_candidate_report_sha256"] as! String else { throw Failure(message: "Preliminary changed") }
        let preliminary = try JSONSerialization.jsonObject(with: preliminaryData) as! [String: Any]
        let known = (preliminary["results"] as! [[String: Any]]).first { $0["id"] as? String == "known-failure" }!
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let start = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "stage": stage, "started": ISO8601DateFormatter().string(from: Date()),
            "plan_sha256": digest(planData), "expected_generations": cases.count]
        var results = [[String: Any]](), totalBytes = 0
        func save() throws {
            report["results"] = results; report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime-start
            report["completed_generations"] = results.count; report["pcm_bytes"] = totalBytes
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for item in cases {
                    let id = item["id"] as! String
                    MLXRandom.seed(UInt64(item["seed"] as! Int))
                    let began = ProcessInfo.processInfo.systemUptime
                    var tokens = [Int](), pcm = Data(), invalid = false
                    try model.generateManaged(text: item["text"] as! String, voice: item["voice"] as! String,
                        language: item["language"] as! String, maxTokens: plan["max_tokens"] as! Int,
                        checkCancellation: {
                            let now = ProcessInfo.processInfo.systemUptime
                            guard !invalid, now-start < plan["per_stage_budget_seconds"] as! Double,
                                  now-began < plan["individual_budget_seconds"] as! Double else { throw Failure(message: "Invalid audio or predeclared budget") }
                        }, onToken: { tokens.append($0) }, onAudioChunk: { chunk in
                            let samples = chunk.asArray(Float.self)
                            guard samples.allSatisfy(\.isFinite) else { invalid = true; return }
                            let values = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                            pcm.append(values.withUnsafeBytes { Data($0) })
                        })
                    guard !invalid, !tokens.isEmpty, !pcm.isEmpty else { throw Failure(message: "Invalid output") }
                    let tokenData = try JSONSerialization.data(withJSONObject: tokens)
                    let pcmHash = digest(pcm)
                    if stage == "candidate" && results.isEmpty {
                        let exact = tokens == known["tokens"] as! [Int] && pcmHash == known["pcm_sha256"] as! String
                        report["preliminary_candidate_parity"] = exact
                        guard exact else { throw Failure(message: "Candidate changed between preliminary and matrix") }
                    }
                    try pcm.write(to: root.appendingPathComponent(id+".pcm"), options: .withoutOverwriting)
                    try tokenData.write(to: root.appendingPathComponent(id+"-tokens.json"), options: .withoutOverwriting)
                    totalBytes += pcm.count
                    var result = item
                    result.merge(["source_group": stage, "reused": false, "token_count": tokens.count, "tokens_sha256": digest(tokenData),
                        "eos_seen": tokens.last == eos, "pcm_sha256": pcmHash, "pcm_bytes": pcm.count,
                        "audio_seconds": Double(pcm.count)/48000, "processing_seconds": ProcessInfo.processInfo.systemUptime-began]) { _, new in new }
                    results.append(result); Memory.clearCache(); try save()
                    print("MATRIX", stage, results.count, "of", cases.count, id, "EOS", tokens.last == eos, "seconds", Double(pcm.count)/48000)
                    guard totalBytes <= plan["maximum_disk_bytes"] as! Int else { throw Failure(message: "Disk budget") }
                }
            }
            guard results.count == cases.count else { throw Failure(message: "Incomplete matrix") }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
