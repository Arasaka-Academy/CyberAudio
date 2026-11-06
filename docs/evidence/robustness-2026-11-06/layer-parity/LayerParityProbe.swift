import Foundation
import Darwin
import CryptoKit
import MLX
import MLXAudioTTS

@main enum LayerParityProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func same(_ a: MLXArray, _ b: MLXArray) -> Bool {
        a.shape == b.shape && a.dtype == b.dtype && a.asType(.float32).asArray(Float.self) == b.asType(.float32).asArray(Float.self)
    }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Layer capture failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "TTS_DIR METALLIB NEW_ROOT PLAN") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output already exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let captureURL = URL(fileURLWithPath: plan["capture_report"] as! String)
        let captureData = try Data(contentsOf: captureURL)
        guard digest(captureData) == plan["capture_report_sha256"] as! String else { throw Failure(message: "Original capture changed") }
        let original = try JSONSerialization.jsonObject(with: captureData) as! [String: Any]
        let prior = original["results"] as! [[String: Any]]
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()), "plan_sha256": digest(planData), "capture_report_sha256": digest(captureData)]
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
                for item in plan["cases"] as! [[String: Any]] {
                    let id = item["id"] as! String
                    let old = prior.first { $0["id"] as? String == id }!
                    let oldSteps = old["steps"] as! [[String: Any]]
                    let directory = root.appendingPathComponent(id, isDirectory: true)
                    try fm.createDirectory(at: directory, withIntermediateDirectories: false)
                    let originalDirectory = captureURL.deletingLastPathComponent().appendingPathComponent(id)
                    var expected = [[String: MLXArray]]()
                    for step in oldSteps {
                        let url = originalDirectory.appendingPathComponent(step["file"] as! String)
                        guard digest(try Data(contentsOf: url)) == step["sha256"] as! String else { throw Failure(message: "Snapshot changed") }
                        expected.append(try MLX.loadArrays(url: url))
                    }
                    let inputs = expected.map { $0["input_embeds"]! }
                    let traces = Set((plan["trace_steps"] as! [String: [Int]])[id]!)
                    let start = ProcessInfo.processInfo.systemUptime
                    var comparisons = [[String: Any]](), snapshots = [[String: Any]]()
                    try model.diagnosticTalkerForward(inputs: inputs, traceSteps: traces) { index, logits, hidden, traced in
                        guard ProcessInfo.processInfo.systemUptime - began < plan["swift_total_budget_seconds"] as! Double else { throw Failure(message: "Time budget") }
                        let logitEqual = same(logits, expected[index]["logits"]!)
                        let hiddenEqual = same(hidden, expected[index]["hidden"]!)
                        comparisons.append(["step": index, "logits_exact": logitEqual, "hidden_exact": hiddenEqual])
                        if !traced.isEmpty {
                            let filename = String(format: "step-%03d.safetensors", index)
                            let url = directory.appendingPathComponent(filename)
                            try MLX.save(arrays: traced, url: url)
                            let data = try Data(contentsOf: url)
                            totalBytes += data.count
                            snapshots.append(["step": index, "file": filename, "bytes": data.count, "sha256": digest(data), "array_count": traced.count])
                            guard totalBytes <= plan["maximum_snapshot_bytes"] as! Int else { throw Failure(message: "Snapshot budget") }
                        }
                    }
                    let exact = comparisons.allSatisfy { $0["logits_exact"] as! Bool && $0["hidden_exact"] as! Bool }
                    results.append(["id": id, "case": item, "steps": comparisons, "snapshots": snapshots, "all_original_native_forward_exact": exact, "processing_seconds": ProcessInfo.processInfo.systemUptime - start])
                    try save()
                    guard exact else { throw Failure(message: "Observation/replay changed native forward output; do not interpret") }
                    Memory.clearCache()
                    print("TRACED", id, "steps", comparisons.count, "snapshots", snapshots.count, "forward exact", exact)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
