import AppKit
import Foundation

/// Runs only inside a dedicated test App with the retained seed-196 Helper.
/// The installed App and default data root are never targets.
@main
@MainActor
struct LegacyFailureProbe {
    struct Failure: Error { let message: String }
    static func require(_ value: Bool, _ message: String) throws { if !value { throw Failure(message: message) } }
    static func until(_ test: () -> Bool, seconds: Double, message: String) async throws {
        let end = ProcessInfo.processInfo.systemUptime + seconds
        while !test() {
            try require(ProcessInfo.processInfo.systemUptime < end, message)
            try await Task.sleep(for: .milliseconds(50))
        }
    }

    static func main() async throws {
        let backend = BackendController()
        try require(backend.root.path.contains("/stream-failure-20261107/legacy-model-data"), "Refuse an unrelated data root")
        let audio = AudioWorkspace(recordingsDirectory: backend.root.appendingPathComponent("recordings"))
        defer { audio.cleanup(); backend.stopForTermination() }
        backend.startPrepared()
        try await until({ backend.ready || backend.status == "failed" }, seconds: 180, message: "Legacy Helper startup did not settle")
        try require(backend.ready, "Legacy Helper failed to start")
        audio.text = "持续验证语音服务的稳定性和恢复能力。"
        audio.voice = "ono_anna"
        audio.ttsLanguage = "chinese"
        audio.instructions = ""
        audio.streaming = true
        let began = ProcessInfo.processInfo.systemUptime
        audio.generate(using: backend)
        var observedPlayback = false
        while audio.generating {
            observedPlayback = observedPlayback || audio.playing
            try require(ProcessInfo.processInfo.systemUptime - began < 180, "Fixed failure did not settle within its original bound")
            try await Task.sleep(for: .milliseconds(50))
        }
        let failure = audio.errorMessage
        try require(observedPlayback, "Actual streamed PCM was never played by the App client")
        try require(failure?.contains("长度上限") == true, "Code-only terminal reason was not recovered: \(failure ?? "nil")")
        try require(audio.generated.isEmpty && !audio.playing, "Actual failed generation exposed partial audio")
        let lines = try String(contentsOf: backend.root.appendingPathComponent("logs/service.log"), encoding: .utf8).components(separatedBy: .newlines)
        let done = try lines.first(where: { $0.contains("request_done id=") && $0.contains("status=failed") })
            .unwrap("Missing failed request in the retained Helper log")
        let marker = try done.range(of: "request_done id=").unwrap("Missing request ID marker")
        let requestID = String(done[marker.upperBound...].prefix(16))
        let record = try await backend.speechRequestStatus(id: requestID)
        try require(record.status == .failed && record.code == "generation_limit" && record.message == nil,
                    "This compatibility control requires an actual old code-only generation_limit record")
        let failureSeconds = ProcessInfo.processInfo.systemUptime - began
        audio.text = "生成之后恢复正常。"
        audio.voice = "serena"
        audio.streaming = false
        audio.generate(using: backend)
        try await until({ !audio.generating }, seconds: 90, message: "Same-instance recovery did not finish")
        try require(audio.errorMessage == nil && audio.generated.count > 44, "Same-instance recovery failed")
        audio.stopPlayback()
        let result: [String: Any] = [
            "status": "passed", "failure_code": record.code!, "legacy_message_field_absent": true,
            "displayed_error": failure!, "failure_elapsed_seconds": failureSeconds,
            "observed_actual_playback": observedPlayback, "partial_audio_export_rejected": true,
            "same_instance_recovery_wav_bytes": audio.generated.count,
            "physical_or_listening_acceptance": false,
        ]
        let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: backend.root.appendingPathComponent("client-result.json"), options: .atomic)
        await backend.stop()
        print(String(decoding: data, as: UTF8.self))
    }
}

private extension Optional {
    func unwrap(_ message: String) throws -> Wrapped {
        guard let value = self else { throw LegacyFailureProbe.Failure(message: message) }
        return value
    }
}
