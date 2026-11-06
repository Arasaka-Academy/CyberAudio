import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum ReferenceParityProbe {
    enum Stop: Error { case captured, budget, disk, nonfinite }
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Numerical capture failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 6 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR PLAN BASELINE") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output already exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let cases = plan["cases"] as! [[String: Any]]
        let cap = plan["max_steps_per_case"] as! Int
        let priorURL = URL(fileURLWithPath: args[5])
        let prior = try JSONSerialization.jsonObject(with: Data(contentsOf: priorURL)) as! [String: Any]
        let failure = prior["failure_observed"] as! [String: Any]
        let priorTokens = failure["tokens"] as! [Int]
        let priorPCM = try Data(contentsOf: priorURL.deletingLastPathComponent().appendingPathComponent(failure["audio_file"] as! String))
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let start = ProcessInfo.processInfo.systemUptime
        var results = [[String: Any]]()
        var totalBytes = 0
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "purpose": plan["purpose"]!, "plan_sha256": digest(planData), "plan": plan,
            "baseline_report_sha256": digest(try Data(contentsOf: priorURL))]
        func saveReport() throws {
            report["results"] = results
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - start
            report["snapshot_bytes"] = totalBytes
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                .write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try saveReport()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for item in cases {
                    let id = item["id"] as! String
                    let directory = root.appendingPathComponent(id, isDirectory: true)
                    try fm.createDirectory(at: directory, withIntermediateDirectories: false)
                    let began = ProcessInfo.processInfo.systemUptime
                    MLXRandom.seed(UInt64(item["seed"] as! Int))
                    var steps = [[String: Any]](), prepared = [String: Any]()
                    var tokens = [Int](), pcm = Data(), invalid = false
                    var reason = "eos"
                    do {
                        try model.diagnosticParityManaged(text: item["text"] as! String, voice: item["voice"] as! String,
                            language: item["language"] as! String, checkCancellation: {
                                if invalid { throw Stop.nonfinite }
                                let now = ProcessInfo.processInfo.systemUptime
                                if now - start > plan["swift_total_budget_seconds"] as! Double ||
                                   now - began > plan["individual_budget_seconds"] as! Double { throw Stop.budget }
                                if steps.count >= cap { throw Stop.captured }
                            }, onToken: { tokens.append($0) }, onAudioChunk: { audio in
                                let samples = audio.asArray(Float.self)
                                guard samples.allSatisfy(\.isFinite) else { invalid = true; return }
                                let values = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                                pcm.append(values.withUnsafeBytes { Data($0) })
                            }, onDiagnostic: { index, arrays in
                                let filename = index < 0 ? "prepared.safetensors" : String(format: "step-%03d.safetensors", index)
                                let file = directory.appendingPathComponent(filename)
                                try MLX.save(arrays: arrays, url: file)
                                let data = try Data(contentsOf: file)
                                totalBytes += data.count
                                var shapes = [String: Any]()
                                for (name, array) in arrays { shapes[name] = ["shape": array.shape, "dtype": String(describing: array.dtype)] }
                                let metadata: [String: Any] = ["step": index, "file": filename, "sha256": digest(data), "bytes": data.count, "arrays": shapes]
                                if index < 0 { prepared = metadata } else { steps.append(metadata) }
                                if totalBytes > plan["maximum_snapshot_bytes"] as! Int { throw Stop.disk }
                            })
                    } catch Stop.captured { reason = "step_limit" }
                    guard !invalid, !steps.isEmpty, !prepared.isEmpty, tokens.count == steps.count else {
                        throw Failure(message: "Incomplete/invalid capture")
                    }
                    let audioFile = directory.appendingPathComponent("captured.pcm")
                    try pcm.write(to: audioFile, options: .withoutOverwriting)
                    var result: [String: Any] = ["id": id, "case": item, "prepared": prepared, "steps": steps,
                        "tokens": tokens, "token_count": tokens.count, "eos_seen": tokens.last == eos, "stop_reason": reason,
                        "pcm_file": "captured.pcm", "pcm_bytes": pcm.count, "pcm_sha256": digest(pcm),
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - began]
                    if id == "known-failure" {
                        let tokenMatch = Array(priorTokens.prefix(tokens.count)) == tokens
                        let pcmMatch = Data(priorPCM.prefix(pcm.count)) == pcm
                        result["original_failure_token_prefix_matches"] = tokenMatch
                        result["original_failure_pcm_prefix_matches"] = pcmMatch
                        guard tokenMatch, pcmMatch else { throw Failure(message: "Capture changed the retained failure trajectory") }
                    }
                    results.append(result)
                    Memory.clearCache()
                    try saveReport()
                    print("CAPTURE", id, "steps", steps.count, "EOS", tokens.last == eos)
                }
            }
            guard results.count == cases.count else { throw Failure(message: "Missing planned case") }
            report["status"] = "diagnostic_completed"
        } catch {
            report["status"] = "failed"; report["error"] = String(describing: error)
            report["finished"] = ISO8601DateFormatter().string(from: Date()); try saveReport()
            throw error
        }
        report["finished"] = ISO8601DateFormatter().string(from: Date())
        try saveReport()
    }
}
