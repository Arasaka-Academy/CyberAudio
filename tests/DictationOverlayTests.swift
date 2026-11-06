import AppKit

@main
@MainActor
struct DictationOverlayTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func main() async {
        do { try await run() }
        catch { fputs("DictationOverlay FAIL: \(error)\n", stderr); exit(1) }
    }
    static func run() async throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let suite = "CyberAudio-overlay-test-\(UUID())"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let audio = AudioWorkspace()
        let controller = DictationController(backend: BackendController(), audio: audio, defaults: defaults)
        defer { controller.cleanup() }
        let originalKeyWindow = NSApp.keyWindow

        // Exercise the real controller and panel before yielding to the hold timer.
        controller.arm(originPID: nil)
        let panel = NSApp.windows.first { $0.identifier?.rawValue == "dictation-overlay" }
        try require(panel?.isVisible == true, "A shortcut press must immediately show the widget before the recording threshold")
        try require(controller.phase == .armed && !audio.recording, "Immediate feedback must not bypass the microphone hold threshold")
        try require(panel?.canBecomeKey == false && panel?.canBecomeMain == false && NSApp.keyWindow === originalKeyWindow,
                    "The widget must preserve the original typing focus")
        controller.cancel(showFeedback: false)
        try require(panel?.isVisible == false && !controller.blocksUpdate, "A short press must dismiss the widget and release update admission")
        try await Task.sleep(for: .milliseconds(250))
        try require(!audio.recording && !audio.requestingMicrophone && controller.phase == .idle,
                    "A cancelled short press must not start recording later")
        print("DictationOverlay: immediate feedback, hold threshold, focus preservation and short-press cleanup passed")
    }
}
