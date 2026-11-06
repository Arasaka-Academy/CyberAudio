import Foundation
import CryptoKit
import Darwin
import MLX
import MLXAudioTTS
import MLXAudioCore

@main enum ReferenceDecodeAdapterProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Reference decoding failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "TTS_DIR METALLIB NEW_ROOT PLAN") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output already exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "plan_sha256": digest(planData), "default_changed": false, "candidate_proposed": false]
        var results = [[String: Any]]()
        func save() throws {
            report["results"] = results
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3TTSModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                guard let window = model.diagnosticDecoderWindow else {
                    throw Failure(message: "Decoder/config missing")
                }
                report["native_sliding_window"] = window
                for item in plan["cases"] as! [[String: Any]] {
                    let start = ProcessInfo.processInfo.systemUptime
                    let id = item["id"] as! String
                    let codesData = try Data(contentsOf: URL(fileURLWithPath: item["codes_file"] as! String))
                    guard digest(codesData) == item["codes_sha256"] as! String else { throw Failure(message: "Codes changed") }
                    let codes = try JSONSerialization.jsonObject(with: codesData) as! [[Int]]
                    guard codes.count == item["sampled_steps"] as! Int, codes.allSatisfy({ $0.count == 16 }) else {
                        throw Failure(message: "Invalid codec shape")
                    }
                    var pcm = Data(), invalid = false
                    let count = try model.diagnosticDecodeCodes(codes, checkCancellation: {
                        let now = ProcessInfo.processInfo.systemUptime
                        guard !invalid, now - began < plan["total_budget_seconds"] as! Double,
                              now - start < plan["individual_budget_seconds"] as! Double else { throw Failure(message: "Diagnostic budget or nonfinite audio") }
                    }, onAudioChunk: { chunk in
                        let audio = chunk.asArray(Float.self)
                        guard audio.allSatisfy(\.isFinite) else { invalid = true; return }
                        let values = audio.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                        pcm.append(values.withUnsafeBytes { Data($0) })
                    })
                    guard !invalid else { throw Failure(message: "Nonfinite audio") }
                    let order = try Data(contentsOf: URL(fileURLWithPath: item["order_pcm_file"] as! String))
                    guard digest(order) == item["order_pcm_sha256"] as! String, pcm.count == count * 1920 * 2 else {
                        throw Failure(message: "Audio size or prior bytes changed")
                    }
                    try pcm.write(to: root.appendingPathComponent(id + ".pcm"), options: .withoutOverwriting)
                    results.append(["id": id, "codec_frames": count, "pcm_bytes": pcm.count,
                        "pcm_sha256": digest(pcm), "codes_sha256": digest(codesData), "prior_order_pcm_exact": pcm == order,
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - start])
                    Memory.clearCache()
                    try save()
                    print("DECODE", id, "frames", count, "prior-order-exact", pcm == order)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date())
        try save()
    }
}
