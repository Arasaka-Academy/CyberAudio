import Foundation

@main @MainActor
struct PreparationTests {
    struct Failure: Error { let message: String }
    static func require(_ value: @autoclosure () -> Bool, _ message: String) throws {
        if !value() { throw Failure(message: message) }
    }
    static func until(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            try require(Date() < deadline, "Preparation timed out")
            try await Task.sleep(for: .milliseconds(10))
        }
    }
    static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-setup-tests-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let helper = root.appendingPathComponent("assets")
        let script = #"""
        #!/bin/sh
        command="$1"; root="$3"
        case "$command" in
          prepare) echo '{"event":"prepared"}';;
          status|set-ffmpeg)
            while [ -f "$root/hang-status" ]; do /bin/sleep 0.05; done
            if [ -f "$root/malformed" ]; then /usr/bin/head -c 70000 /dev/zero | /usr/bin/tr '\000' x; exit 0; fi
            ready=false; if [ -f "$root/ready" ]; then ready=true; fi
            echo "{\"event\":\"status\",\"total_bytes\":100,\"verified_bytes\":0,\"resumable_bytes\":20,\"missing_files\":2,\"invalid_files\":[],\"invalid_partials\":[],\"models_ready\":$ready,\"ffmpeg_ready\":true,\"ffmpeg_path\":\"/ffmpeg\",\"available_bytes\":1000000000,\"required_bytes\":100,\"can_download\":true}";;
          verify) echo '{"event":"verified"}';;
          download)
            /usr/bin/printf '%s\n' "$@" > "$root/download-arguments"
            echo '{"event":"progress","path":"asr/model","completed_bytes":50,"total_bytes":100,"phase":"downloading"}'
            while [ -f "$root/hold" ]; do /bin/sleep 0.05; done
            if [ -f "$root/fail" ]; then echo '{"event":"error","code":"download_unavailable","message":"请检查网络后继续。"}'; exit 1; fi
            /usr/bin/touch "$root/ready"
            echo '{"event":"verified"}';;
        esac
        """#
        try Data(script.utf8).write(to: helper)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
        func flag(_ name: String) throws { try Data().write(to: root.appendingPathComponent(name)) }
        func remove(_ name: String) { try? FileManager.default.removeItem(at: root.appendingPathComponent(name)) }
        var starts = 0
        let setup = PreparationController(root: root, helper: helper, onReady: { starts += 1 })
        setup.check()
        try await until { !setup.busy }
        try require(setup.phase == .needsSetup && starts == 0 && setup.snapshot?.resumable_bytes == 20, "First launch must wait for explicit download")

        try flag("hold")
        setup.download()
        try await until { setup.completedBytes == 50 }
        let defaultArguments = try String(contentsOf: root.appendingPathComponent("download-arguments"), encoding: .utf8)
        try require(defaultArguments.contains("--source\nhuggingface\n"), "The selected model source must reach the owned download helper")
        var installed = false
        let gate = UpdateGate(audioBusy: { setup.busy }, prepare: { _ in true }, resume: { _ in })
        gate.begin { installed = true }
        try await Task.sleep(for: .milliseconds(250))
        try require(!installed, "Updates must wait for the owned preparation helper")
        setup.cancel()
        try require(setup.busy, "Cancellation must retain ownership until the helper exits")
        try await until { !setup.busy }
        try require(setup.phase == .cancelled && starts == 0, "A cancelled helper must never start inference")
        try await until { installed }
        remove("hold")

        try flag("fail")
        setup.downloadSource = .modelScope
        setup.proxy = "  http://127.0.0.1:7890  "
        setup.download()
        setup.downloadSource = .huggingFace
        try await until { !setup.busy }
        try require(setup.phase == .failed && setup.message.contains("网络") && starts == 0, "Download failures must remain recoverable")
        let selectedArguments = try String(contentsOf: root.appendingPathComponent("download-arguments"), encoding: .utf8)
        try require(selectedArguments.contains("--source\nmodelscope\n") && selectedArguments.contains("--proxy\nhttp://127.0.0.1:7890\n"),
                    "A download must retain its selected source and proxy for the whole attempt")
        setup.proxy = ""
        remove("fail")
        setup.download()
        try await until { !setup.busy }
        try require(setup.phase == .ready && starts == 1, "Retry must verify assets and then start exactly once")
        setup.check(); setup.check()
        try await until { !setup.busy }
        try require(starts == 2, "Repeated start requests must share one preparation attempt")

        let invalid = PreparationController(root: root, helper: helper, onReady: { starts += 1 })
        try flag("malformed")
        invalid.check()
        try await until { !invalid.busy }
        try require(invalid.phase == .failed && starts == 2, "Oversized helper output must fail closed")
        remove("malformed"); remove("ready")
        try flag("hang-status")
        let hanging = PreparationController(root: root, helper: helper, commandTimeout: 0.2, onReady: { starts += 1 })
        hanging.check(); try await until { !hanging.busy }
        try require(hanging.phase == .failed && hanging.message.contains("超时") && starts == 2, "An unresponsive helper must time out without releasing ownership early")
        remove("hang-status")

        let quitting = PreparationController(root: root, helper: helper, onReady: { starts += 1 })
        quitting.check(); try await until { !quitting.busy }
        try flag("hold")
        quitting.download(); try await until { quitting.completedBytes == 50 }
        quitting.shutdown(); remove("hold")
        try await Task.sleep(for: .milliseconds(150))
        try require(starts == 2, "Late helper completion after quit must not start inference")
        print("Preparation: first launch, fixed source selection, progress, cancellation ownership, update drain, failure/retry, reuse, bounded output and quit passed")
    }
}
