import Foundation
import CryptoKit
import Darwin
import MLX
import MLXAudioTTS
import MLXAudioCore

@main enum ReferenceDecodeProbe {
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
                guard let decoder = model.speechTokenizer?.decoder, let config = model.config.talkerConfig else {
                    throw Failure(message: "Decoder/config missing")
                }
                report["native_sliding_window"] = decoder.config.slidingWindow
                for item in plan["cases"] as! [[String: Any]] {
                    let start = ProcessInfo.processInfo.systemUptime
                    let id = item["id"] as! String
                    let codesData = try Data(contentsOf: URL(fileURLWithPath: item["codes_file"] as! String))
                    guard digest(codesData) == item["codes_sha256"] as! String else { throw Failure(message: "Codes changed") }
                    var codes = try JSONSerialization.jsonObject(with: codesData) as! [[Int]]
                    guard codes.count == item["sampled_steps"] as! Int, codes.allSatisfy({ $0.count == 16 }) else {
                        throw Failure(message: "Invalid codec shape")
                    }
                    if item["eos_seen"] as! Bool {
                        guard codes.last?.first == config.codecEosTokenId else { throw Failure(message: "Missing EOS") }
                        codes.removeLast()
                    }
                    guard !codes.isEmpty, codes.allSatisfy({ $0.first != config.codecEosTokenId }) else { throw Failure(message: "Invalid audio codec") }
                    decoder.resetStreamingState()
                    defer { decoder.resetStreamingState() }
                    var pcm = Data()
                    for index in stride(from: 0, to: codes.count, by: 4) {
                        let now = ProcessInfo.processInfo.systemUptime
                        guard now - began < plan["total_budget_seconds"] as! Double,
                              now - start < plan["individual_budget_seconds"] as! Double else { throw Failure(message: "Diagnostic budget") }
                        let part = codes[index..<min(index + 4, codes.count)]
                        let array = MLXArray(part.flatMap { $0.map { Int32($0) } }).reshaped(1, part.count, 16).transposed(0, 2, 1)
                        let audio = decoder.streamingStep(array).asArray(Float.self)
                        guard audio.allSatisfy(\.isFinite) else { throw Failure(message: "Nonfinite audio") }
                        let values = audio.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                        pcm.append(values.withUnsafeBytes { Data($0) })
                    }
                    let order = try Data(contentsOf: URL(fileURLWithPath: item["order_pcm_file"] as! String))
                    guard digest(order) == item["order_pcm_sha256"] as! String, pcm.count == codes.count * 1920 * 2 else {
                        throw Failure(message: "Audio size or prior bytes changed")
                    }
                    try pcm.write(to: root.appendingPathComponent(id + ".pcm"), options: .withoutOverwriting)
                    results.append(["id": id, "codec_frames": codes.count, "pcm_bytes": pcm.count,
                        "pcm_sha256": digest(pcm), "codes_sha256": digest(codesData), "prior_order_pcm_exact": pcm == order,
                        "processing_seconds": ProcessInfo.processInfo.systemUptime - start])
                    Memory.clearCache()
                    try save()
                    print("DECODE", id, "frames", codes.count, "prior-order-exact", pcm == order)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        report["finished"] = ISO8601DateFormatter().string(from: Date())
        try save()
    }
}
