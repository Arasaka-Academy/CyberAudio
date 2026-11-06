import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum CompleteCodecProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Complete codec capture failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "TTS_DIR METALLIB NEW_ROOT PLAN") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output already exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let baselineURL = URL(fileURLWithPath: plan["candidate_report"] as! String)
        let baselineData = try Data(contentsOf: baselineURL)
        guard digest(baselineData) == plan["candidate_report_sha256"] as! String else { throw Failure(message: "Baseline report changed") }
        let baseline = try JSONSerialization.jsonObject(with: baselineData) as! [String: Any]
        let prior = baseline["results"] as! [[String: Any]]
        guard prior.count == 6 else { throw Failure(message: "Wrong original cohort") }
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "plan_sha256": digest(planData), "candidate_report_sha256": digest(baselineData)]
        var results = [[String: Any]](), totalBytes = 0
        func save() throws {
            report["results"] = results; report["snapshot_bytes"] = totalBytes
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
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
                    let original = prior.first { $0["id"] as? String == id }!
                    let originalPCM = try Data(contentsOf: baselineURL.deletingLastPathComponent().appendingPathComponent(original["pcm_file"] as! String))
                    guard digest(originalPCM) == original["pcm_sha256"] as! String else { throw Failure(message: "Original PCM changed") }
                    let directory = root.appendingPathComponent(id, isDirectory: true)
                    try fm.createDirectory(at: directory, withIntermediateDirectories: false)
                    let start = ProcessInfo.processInfo.systemUptime
                    MLXRandom.seed(UInt64(item["seed"] as! Int))
                    var tokens = [Int](), pcm = Data(), invalid = false
                    var steps = [[String: Any]](), prepared = [String: Any]()
                    try model.diagnosticCompleteManaged(text: item["text"] as! String, voice: item["voice"] as! String,
                        language: item["language"] as! String, checkCancellation: {
                            let now = ProcessInfo.processInfo.systemUptime
                            guard !invalid, now - began < plan["swift_total_budget_seconds"] as! Double,
                                  now - start < plan["individual_budget_seconds"] as! Double else { throw Failure(message: "Diagnostic budget or invalid audio") }
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
                            guard totalBytes <= plan["maximum_snapshot_bytes"] as! Int else { throw Failure(message: "Snapshot budget") }
                        })
                    guard !invalid, !steps.isEmpty, !prepared.isEmpty, tokens.count == steps.count else { throw Failure(message: "Incomplete capture") }
                    try pcm.write(to: directory.appendingPathComponent("captured.pcm"), options: .withoutOverwriting)
                    let tokenParity = tokens == original["tokens"] as! [Int]
                    let pcmParity = pcm == originalPCM
                    results.append(["id": id, "case": item, "prepared": prepared, "steps": steps,
                        "tokens": tokens, "token_count": tokens.count, "eos_seen": tokens.last == eos,
                        "pcm_file": "captured.pcm", "pcm_bytes": pcm.count, "pcm_sha256": digest(pcm),
                        "original_candidate_tokens_exact": tokenParity, "original_candidate_pcm_exact": pcmParity,
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - start])
                    try save()
                    guard tokenParity, pcmParity else { throw Failure(message: "Instrumentation changed original candidate output; do not interpret captures") }
                    Memory.clearCache()
                    print("COMPLETE", id, "steps", steps.count, "original PCM/tokens exact", tokenParity && pcmParity)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
