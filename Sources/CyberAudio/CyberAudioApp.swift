import AppKit
import SwiftUI

@MainActor
final class AppState {
    static let shared = AppState()
    let backend = BackendController()
    let audio = AudioWorkspace()
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var terminating = false
    private var terminationSignal: DispatchSourceSignal?

    func applicationDidFinishLaunching(_ notification: Notification) {
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { NSApp.terminate(nil) }
        source.resume()
        terminationSignal = source
        AppState.shared.backend.start()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateNow }
        terminating = true
        AppState.shared.audio.cleanup()
        AppState.shared.backend.stopForTermination()
        return .terminateNow
    }
}

@main
struct CyberAudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var backend = AppState.shared.backend
    @StateObject private var audio = AppState.shared.audio
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    var body: some Scene {
        Window("CyberAudio", id: "main") {
            ContentView().environmentObject(backend).environmentObject(audio)
        }
        .defaultSize(width: 1040, height: 760)
        .windowResizability(.contentMinSize)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("显示 CyberAudio") { showMain() }.keyboardShortcut("0", modifiers: .command)
            }
            CommandMenu("音频") {
                Button(audio.recording ? "停止并识别" : "开始录音") {
                    Task {
                        if audio.recording { await audio.stopAndTranscribe(using: backend) }
                        else { await audio.beginRecording() }
                    }
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .disabled(!audio.recording && (!backend.ready || backend.busy || audio.working))
                Button("取消任务") {
                    if audio.transcribing { audio.cancelTranscription() }
                    else { audio.cancelGeneration() }
                }
                .keyboardShortcut(".", modifiers: .command).disabled(!audio.generating && !audio.transcribing)
            }
        }

        Settings {
            ServiceSettingsView().environmentObject(backend).environmentObject(audio)
        }

        MenuBarExtra("CyberAudio · \(backend.statusLabel)", systemImage: backend.busy ? "waveform" : "waveform.circle") {
            Text("CyberAudio · \(backend.statusLabel)")
            Button("显示主窗口") { showMain() }
            Button("设置…") {
                openSettings()
                NSApp.activate(ignoringOtherApps: true)
            }.keyboardShortcut(",", modifiers: .command)
            Divider()
            if backend.running {
                Button("停止引擎") { Task { await backend.stop() } }.disabled(audio.working)
            } else {
                Button("启动引擎") { backend.start() }
            }
            Button("重启引擎") { Task { await backend.restart() } }
                .disabled(audio.working || backend.status == "stopping")
            Divider()
            Button("退出 CyberAudio") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
    }

    private func showMain() {
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}
