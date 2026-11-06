import Foundation
import Darwin
import MLX
import MLXAudioTTS

@main enum ModelPreservationProbe {
    static func main() async {
        setbuf(stdout, nil)
        guard CommandLine.arguments.count == 4 else { exit(64) }
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let report = URL(fileURLWithPath: CommandLine.arguments[3])
        GPU.metallib = URL(fileURLWithPath: CommandLine.arguments[2])
        Memory.cacheLimit = 512 * 1024 * 1024
        let start = ProcessInfo.processInfo.systemUptime
        var rejected = false
        var recognizedError = false
        do {
            _ = try await Device.withDefaultDevice(.gpu) {
                try await Qwen3TTSModel.fromModelDirectory(root)
            }
        } catch {
            rejected = true
            recognizedError = String(describing: error).contains("speech_tokenizer")
        }
        let fm = FileManager.default
        let sentinel = root.appendingPathComponent("preserve-user-data.txt")
        let tokenizer = root.appendingPathComponent("speech_tokenizer")
        let retained = (try? Data(contentsOf: sentinel)) == Data("User data must survive a failed model load".utf8)
            && (try? Data(contentsOf: tokenizer)) == Data("Unexpected user file".utf8)
        let result: [String: Any] = [
            "status": rejected && recognizedError && retained ? "passed" : "failed",
            "rejected": rejected, "recognized_tokenizer_error": recognizedError,
            "model_root_exists": fm.fileExists(atPath: root.path),
            "sentinel_and_user_file_preserved": retained,
            "elapsed_seconds": ProcessInfo.processInfo.systemUptime - start,
            "scope": "Actual pinned Qwen3TTSModel.fromModelDirectory with real weights linked into a disposable directory",
        ]
        do {
            try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
                .write(to: report, options: [.atomic])
        } catch { exit(3) }
        exit(rejected && recognizedError && retained ? 0 : 2)
    }
}
