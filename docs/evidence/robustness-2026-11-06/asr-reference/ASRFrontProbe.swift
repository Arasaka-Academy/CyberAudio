import Foundation
import Darwin
import CryptoKit
import MLX
import MLXAudioCore
import MLXAudioSTT

@main enum ASRFrontProbe {
    struct Failure: Error { let message: String }
    static func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() } catch { fputs("ASR front capture: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 6 else { throw Failure(message: "MODEL METALLIB PLAN NATIVE_REPORT NEW_OUTPUT") }
        let fm = FileManager.default
        let root = URL(fileURLWithPath: args[5], isDirectory: true)
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Preserve earlier output") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[3]))
        let nativeData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let native = try JSONSerialization.jsonObject(with: nativeData) as! [String: Any]
        let originals = Dictionary(uniqueKeysWithValues: (native["results"] as! [[String: Any]]).map { ($0["id"] as! String, $0["response"] as! [String: Any]) })
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var results = [[String: Any]]()
        var report: [String: Any] = ["status": "running", "plan_sha256": sha(planData), "native_report_sha256": sha(nativeData)]
        func save() throws {
            report["results"] = results
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            try await Device.withDefaultDevice(.gpu) {
                let model = try await Qwen3ASRModel.fromModelDirectory(URL(fileURLWithPath: args[1], isDirectory: true))
                var bytes = 0
                for item in plan["cases"] as! [[String: Any]] {
                    let id = item["id"] as! String
                    let source = URL(fileURLWithPath: item["audio"] as! String)
                    guard sha(try Data(contentsOf: source)) == item["audio_sha256"] as! String else { throw Failure(message: "Input changed") }
                    let (_, samples) = try loadAudioArray(from: source, sampleRate: 16000)
                    let values = samples.asArray(Float.self)
                    guard values.allSatisfy(\.isFinite) else { throw Failure(message: "Nonfinite input") }
                    let control = try model.generateManaged(audio: samples, maxTokens: 1024, temperature: 0, language: nil, checkCancellation: {
                        guard ProcessInfo.processInfo.systemUptime - began < 600 else { throw Failure(message: "Capture budget") }
                    })
                    guard control.text.trimmingCharacters(in: .whitespacesAndNewlines) == originals[id]!["text"] as! String else { throw Failure(message: "Original HTTP text does not reproduce") }
                    let (mel, mask, count) = model.preprocessAudio(samples)
                    let prompt = model.buildPrompt(numAudioTokens: count, context: "", language: nil)
                    let encoded = model.getAudioFeatures(mel, featureAttentionMask: mask)
                    MLX.eval(mel, mask, prompt, encoded)
                    let file = root.appendingPathComponent(id + ".safetensors")
                    try MLX.save(arrays: ["audio": samples, "mel": mel, "mask": mask, "prompt": prompt, "encoded": encoded], url: file)
                    let data = try Data(contentsOf: file)
                    bytes += data.count
                    guard bytes < 120 * 1024 * 1024 else { throw Failure(message: "Snapshot byte budget") }
                    results.append(["id": id, "file": file.lastPathComponent, "sha256": sha(data), "bytes": data.count,
                                    "num_audio_tokens": count, "native_http_text_exact": true, "text": control.text,
                                    "input_float32_sha256": values.withUnsafeBytes { sha(Data($0)) }])
                    Memory.clearCache(); try save()
                    print("CAPTURE", id, count)
                }
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        try save()
    }
}
