import Foundation
import Darwin
import CryptoKit
import MLX
import MLXRandom
import MLXAudioCore
import MLXAudioTTS

@main enum TextLayoutProbe {
    struct Failure: Error { let message: String }
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func main() async {
        setbuf(stdout, nil)
        do { try await run() }
        catch { fputs("Text layout diagnostic failed: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        let args = CommandLine.arguments
        guard args.count == 6 else { throw Failure(message: "TTS_DIR METALLIB NEW_OUTPUT_DIR PLAN ORIGINAL_FAILURE_REPORT") }
        let root = URL(fileURLWithPath: args[3], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[4]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        let cases = plan["cases"] as! [[String: Any]], modes = plan["modes"] as! [String]
        let expected = plan["expected_generations"] as! Int
        guard expected == cases.count * modes.count else { throw Failure(message: "Plan size mismatch") }
        let failureData = try Data(contentsOf: URL(fileURLWithPath: args[5]))
        let original = try JSONSerialization.jsonObject(with: failureData) as! [String: Any]
        let failure = original["failure_observed"] as! [String: Any]
        let failureTokens = failure["tokens"] as! [Int]
        let failurePCM = try Data(contentsOf: URL(fileURLWithPath: args[5]).deletingLastPathComponent()
            .appendingPathComponent(failure["audio_file"] as! String))
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2]); Memory.cacheLimit = 512 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "started": ISO8601DateFormatter().string(from: Date()),
            "plan": plan, "plan_sha256": digest(planData), "original_failure_report_sha256": digest(failureData)]
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
                let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("config.json"))) as! [String: Any]
                let eos = (config["talker_config"] as! [String: Any])["codec_eos_token_id"] as! Int
                for item in cases {
                    for mode in modes {
                        let id = item["id"] as! String, label = id + "-" + mode
                        let begin = ProcessInfo.processInfo.systemUptime
                        MLXRandom.seed(UInt64(item["seed"] as! Int))
                        var tokens = [Int](), audio = Data(), invalid = false
                        try model.diagnosticTextLayoutManaged(text: item["text"] as! String, voice: item["voice"] as! String,
                            language: item["language"] as! String, sequentialText: mode == "sequential",
                            checkCancellation: {
                                let now = ProcessInfo.processInfo.systemUptime
                                if now - began > plan["total_budget_seconds"] as! Double || now - begin > plan["individual_budget_seconds"] as! Double {
                                    throw Failure(message: "Predeclared diagnostic deadline reached")
                                }
                                if invalid { throw Failure(message: "Nonfinite audio") }
                            }, onToken: { tokens.append($0) }, onAudioChunk: { chunk in
                                let values = chunk.asArray(Float.self)
                                guard values.allSatisfy(\.isFinite) else { invalid = true; return }
                                let pcm = values.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                                audio.append(pcm.withUnsafeBytes { Data($0) })
                            })
                        guard !invalid else { throw Failure(message: "Nonfinite audio") }
                        let tokenData = try JSONSerialization.data(withJSONObject: tokens)
                        try tokenData.write(to: root.appendingPathComponent(label + "-tokens.json"), options: .withoutOverwriting)
                        try audio.write(to: root.appendingPathComponent(label + ".pcm"), options: .withoutOverwriting)
                        totalBytes += audio.count
                        var row = item
                        row.merge(["id": label, "case_id": id, "mode": mode, "eos_seen": tokens.last == eos,
                            "token_count": tokens.count, "tokens_sha256": digest(tokenData), "pcm_sha256": digest(audio),
                            "audio_seconds": Double(audio.count) / 48000,
                            "processing_seconds": ProcessInfo.processInfo.systemUptime - begin]) { _, value in value }
                        if id == "known-failure", mode == "prefill" {
                            let parity = tokens == failureTokens && audio == failurePCM
                            row["original_failure_parity"] = parity
                            results.append(row); try save()
                            guard parity else { throw Failure(message: "Original failure parity missing") }
                        } else { results.append(row); try save() }
                        print("CASE", results.count, "of", expected, label, "EOS", row["eos_seen"]!, "seconds", row["audio_seconds"]!)
                        guard totalBytes <= plan["maximum_disk_bytes"] as! Int else { throw Failure(message: "Predeclared disk bound reached") }
                        Memory.clearCache()
                    }
                }
            }
            guard results.count == expected else { throw Failure(message: "Incomplete diagnostic") }
            report["status"] = "diagnostic_completed"
        } catch {
            report["status"] = "failed"; report["error"] = String(describing: error)
            report["finished"] = ISO8601DateFormatter().string(from: Date()); try save(); throw error
        }
        report["finished"] = ISO8601DateFormatter().string(from: Date()); try save()
    }
}
