import AppKit
import SwiftUI

/// Real AppKit/SwiftUI rendering, independent of screen capture and keyboard permissions.
@main
@MainActor
struct WorkspaceLayoutTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func descendants<T: NSView>(_ view: NSView, of type: T.Type) -> [T] {
        let own: [T] = (view as? T).map { [$0] } ?? []
        return own + view.subviews.flatMap { descendants($0, of: type) }
    }
    static func render<V: View>(_ view: V, size: NSSize, name: String, output: URL, editors: Int?) throws -> NSHostingView<V> {
        let host = NSHostingView(rootView: view)
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        window.setContentSize(size)
        RunLoop.main.run(until: Date().addingTimeInterval(0.08))
        host.layoutSubtreeIfNeeded()
        let scrolls = descendants(host, of: NSScrollView.self)
        if let editors { try require(scrolls.count == editors, "\(name): unexpected outer scroll view (\(scrolls.count))") }
        for scroll in editors == nil ? [] : scrolls {
            try require(!scroll.hasVerticalScroller && !scroll.hasHorizontalScroller, "\(name): scrollers must remain disabled under Always")
            let rect = scroll.convert(scroll.bounds, to: host)
            try require(host.bounds.insetBy(dx: -1, dy: -1).contains(rect), "\(name): text editor extends beyond the minimum window")
            try require(rect.height > 80 && rect.width > 200, "\(name): primary editor has been compressed")
        }
        try require(host.bounds.width <= size.width + 1 && host.bounds.height <= size.height + 1, "\(name): content exceeds requested size")
        guard let image = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { throw Failure(message: "\(name): no native render") }
        host.cacheDisplay(in: host.bounds, to: image)
        guard let png = image.representation(using: .png, properties: [:]) else { throw Failure(message: "\(name): no PNG") }
        try png.write(to: output.appendingPathComponent(name + ".png"))
        if let editor = scrolls.first?.documentView as? NSTextView, name == "stt-long-dark", let scroll = scrolls.first {
            editor.scrollRangeToVisible(NSRange(location: (editor.string as NSString).length, length: 0))
            try require(scroll.contentView.bounds.minY > 0, "Long text must scroll internally even without scrollers")
            editor.setSelectedRange(NSRange(location: 0, length: 2))
            editor.insertText("编辑", replacementRange: editor.selectedRange())
            try require(editor.string.hasPrefix("编辑"), "The native editor must preserve normal selection replacement")
            editor.breakUndoCoalescing()
            editor.undoManager?.undo()
            try require(editor.string.hasPrefix("长文本"), "Native undo must restore edited Unicode text")
            editor.setSelectedRange(NSRange(location: 0, length: 0))
            editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0), replacementRange: NSRange(location: NSNotFound, length: 0))
            try require(editor.hasMarkedText(), "The native editor accepts IME marked text")
            editor.unmarkText()
        }
        // Keep the host/window alive until capture is complete, then detach without a close callback.
        window.contentView = nil
        return host
    }
    static func main() async throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        UserDefaults.standard.set("Always", forKey: "AppleShowScrollBars")
        let output = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? ".context/compact-dictation-20261106/layout", isDirectory: true)
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let backend = BackendController()
        let audio = AudioWorkspace(recordingsDirectory: output.appendingPathComponent("recordings", isDirectory: true))
        let defaults = UserDefaults(suiteName: "CyberAudio-LayoutTests-\(UUID())")!
        defaults.set(false, forKey: "DictationEnabled")
        let dictation = DictationController(backend: backend, audio: audio, defaults: defaults)
        let updater = UpdaterController(backend: backend, audio: audio, dictation: dictation)
        func workspace(_ mode: WorkspaceMode, _ name: String, dark: Bool = false) throws {
            NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
            let view = ContentView(initialMode: mode).environmentObject(backend).environmentObject(audio)
                .environmentObject(updater).environmentObject(dictation)
                .environment(\.colorScheme, dark ? .dark : .light)
            _ = try render(view, size: NSSize(width: 560, height: 440), name: name, output: output, editors: 1)
        }
        try workspace(.transcription, "stt-light")
        audio.transcript = String(repeating: "长文本内部滚动。中文 English 123。\n", count: 200)
        try workspace(.transcription, "stt-long-dark", dark: true)
        audio.recording = true; audio.recordingSeconds = 37
        try workspace(.transcription, "stt-recording")
        audio.recording = false
        audio.text = String(repeating: "表达清楚，语气自然。", count: 50)
        audio.instructions = String(repeating: "自然清晰", count: 50)
        try workspace(.speech, "tts-maximum")
        audio.generating = true
        try workspace(.speech, "tts-generating-dark", dark: true)
        audio.generating = false
        audio.reportError(CyberAudioError.message(String(repeating: "很长的错误说明不会遮挡操作。", count: 100)), for: .speech)
        try workspace(.speech, "tts-long-error")
        audio.errorMessage = nil
        audio.generating = true
        updater.installWhenIdle({})
        try workspace(.speech, "tts-update-waiting")
        updater.gate.cancel(); audio.generating = false
        for tab in 0..<3 {
            let view = ServiceSettingsView(initialTab: tab).environmentObject(backend).environmentObject(audio)
                .environmentObject(updater).environmentObject(dictation)
            _ = try render(view, size: NSSize(width: 620, height: 560), name: "settings-\(tab)", output: output, editors: nil)
        }
        let setupRoot = output.appendingPathComponent("setup-fixture")
        try FileManager.default.createDirectory(at: setupRoot, withIntermediateDirectories: true)
        let setupHelper = setupRoot.appendingPathComponent("assets")
        let fixture = #"""
        #!/bin/sh
        case "$1" in
          prepare) echo '{"event":"prepared"}';;
          status) echo '{"event":"status","total_bytes":5547997530,"verified_bytes":0,"resumable_bytes":0,"missing_files":23,"invalid_files":[],"invalid_partials":[],"models_ready":false,"ffmpeg_ready":false,"ffmpeg_path":"/missing/ffmpeg","available_bytes":20000000000,"required_bytes":8000000000,"can_download":true}';;
          download)
            echo '{"event":"progress","path":"asr/model.safetensors","completed_bytes":2400000000,"total_bytes":5547997530,"phase":"downloading"}'
            while [ -f "$3/hold" ]; do /bin/sleep 0.05; done
            echo '{"event":"error","message":"下载中断，已保留进度；请检查网络或代理后继续。"}'; exit 1;;
        esac
        """#
        try Data(fixture.utf8).write(to: setupHelper)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: setupHelper.path)
        let setup = PreparationController(root: setupRoot, helper: setupHelper, onReady: {})
        func setupRender(_ name: String, dark: Bool = false) throws {
            NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
            _ = try render(PreparationView(preparation: setup).environment(\.colorScheme, dark ? .dark : .light),
                           size: NSSize(width: 560, height: 440), name: name, output: output, editors: nil)
        }
        func waitForSetup(_ condition: () -> Bool) async throws {
            let deadline = Date().addingTimeInterval(5)
            while !condition() {
                try require(Date() < deadline, "Setup layout fixture timed out")
                try await Task.sleep(for: .milliseconds(10))
            }
        }
        try setupRender("setup-checking")
        setup.check(); try await waitForSetup { !setup.busy }
        try setupRender("setup-missing-ffmpeg")
        setup.downloadSource = .modelScope
        try setupRender("setup-modelscope")
        let hold = setupRoot.appendingPathComponent("hold")
        try Data().write(to: hold)
        setup.download(); try await waitForSetup { setup.completedBytes > 0 }
        try setupRender("setup-downloading-dark", dark: true)
        setup.cancel(); try await waitForSetup { !setup.busy }
        try FileManager.default.removeItem(at: hold)
        setup.download(); try await waitForSetup { !setup.busy }
        try setupRender("setup-failed")
        setup.shutdown()
        audio.cleanup(); dictation.cleanup()
        print("WorkspaceLayout: 7 workspace states, 3 settings tabs and 5 preparation states rendered at minimum size")
        print("Native view captures: \(output.path)")
    }
}
