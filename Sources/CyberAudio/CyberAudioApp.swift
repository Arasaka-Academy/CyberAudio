import AppKit
import SwiftUI

@MainActor
final class AppState {
    static let shared = AppState()
    let backend: BackendController
    let audio: AudioWorkspace
    let preparation: PreparationController
    lazy var dictation = DictationController(backend: backend, audio: audio)
    lazy var models = ModelManagerController(backend:backend,audio:audio,dictation:dictation,preparation:preparation)
    lazy var updater = UpdaterController(backend: backend, audio: audio, dictation: dictation,
                                         preparationBusy: { [preparation] in preparation.busy })

    private init() {
        let backend = BackendController()
        self.backend = backend
        let audio = AudioWorkspace(recordingsDirectory: backend.root.appendingPathComponent("tmp/recordings", isDirectory: true))
        self.audio = audio
        let preparation = PreparationController(root: backend.root) { [weak backend, weak audio] in
            if audio?.preparingUpdate == false { backend?.startPrepared() }
        }
        self.preparation = preparation
        backend.requestPreparation = { [weak preparation, weak audio] in
            if audio?.preparingUpdate == false,audio?.managingModels == false { preparation?.check() }
        }
    }
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
        AppState.shared.preparation.check()
        AppState.shared.updater.start()
        AppState.shared.dictation.start()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateNow }
        let updater = AppState.shared.updater
        if updater.pendingInstallation, !updater.gate.terminationAllowed {
            guard !updater.gate.waiting else { return .terminateCancel }
            updater.installWhenIdle({ [weak self] in
                self?.finishTermination()
                sender.reply(toApplicationShouldTerminate: true)
            }, cancelled: { sender.reply(toApplicationShouldTerminate: false) })
            return .terminateLater
        }
        finishTermination()
        return .terminateNow
    }

    private func finishTermination() {
        terminating = true
        AppState.shared.models.shutdown()
        AppState.shared.preparation.shutdown()
        AppState.shared.dictation.cleanup()
        AppState.shared.audio.cleanup()
        AppState.shared.backend.stopForTermination()
    }
}

@main
struct CyberAudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var backend = AppState.shared.backend
    @StateObject private var audio = AppState.shared.audio
    @StateObject private var updater = AppState.shared.updater
    @StateObject private var dictation = AppState.shared.dictation
    @StateObject private var preparation = AppState.shared.preparation
    @StateObject private var models = AppState.shared.models
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    var body: some Scene {
        Window("CyberAudio", id: "main") {
            Group {
                if preparation.ready { ContentView() }
                else { PreparationView(preparation: preparation) }
            }.environmentObject(backend).environmentObject(audio).environmentObject(updater).environmentObject(dictation)
        }
        .defaultSize(width: 560, height: 440)
        .windowResizability(.contentMinSize)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(after: .appInfo) {
                Button(updater.menuTitle) { updater.checkForUpdates() }.disabled(!updater.canPerformAction)
                Button("显示 CyberAudio") { showMain() }.keyboardShortcut("0", modifiers: .command)
            }
            CommandMenu("音频") {
                Button(audio.recording ? "停止并识别" : "开始录音") {
                    Task {
                        if audio.recording { await audio.stopAndTranscribe(using: backend) }
                        else { await audio.beginRecording() }
                    }
                }
                .disabled(dictation.blocksUpdate || !audio.recording && (!backend.ready || backend.busy || audio.cannotStart))
                Button("取消任务") {
                    if dictation.blocksUpdate { dictation.cancel() }
                    else if audio.transcribing { audio.cancelTranscription() }
                    else { audio.cancelGeneration() }
                }
                .keyboardShortcut(".", modifiers: .command).disabled(!dictation.blocksUpdate && !audio.generating && !audio.transcribing)
            }
        }

        Settings {
            ServiceSettingsView().environmentObject(backend).environmentObject(audio).environmentObject(updater).environmentObject(dictation)
                .environmentObject(preparation).environmentObject(models)
        }

        MenuBarExtra {
            Text("CyberAudio · \(backend.statusLabel)")
            Button("显示主窗口") { showMain() }
            Button(dictation.enabled ? "暂停快捷听写" : "启用快捷听写") { dictation.setEnabled(!dictation.enabled) }
            Text("长按 \(dictation.shortcut.display) 听写")
            if let version = updater.availableVersion { Text("新版本 \(version)") }
            Button(updater.menuTitle) { updater.checkForUpdates() }.disabled(!updater.canPerformAction)
            Button("设置…") {
                openSettings()
                NSApp.activate(ignoringOtherApps: true)
            }.keyboardShortcut(",", modifiers: .command)
            Divider()
            if backend.running {
                Button("停止引擎") { Task { await backend.stop() } }.disabled(audio.cannotStart || dictation.blocksUpdate)
            } else {
                Button("启动引擎") { backend.start() }.disabled(audio.preparingUpdate || audio.managingModels)
            }
            Button("重启引擎") { Task { await backend.restart() } }
                .disabled(audio.cannotStart || dictation.blocksUpdate || backend.status == "stopping")
            Divider()
            Button("退出 CyberAudio") { NSApp.terminate(nil) }.keyboardShortcut("q")
        } label: {
            Label {
                Text("CyberAudio · \(backend.statusLabel)")
            } icon: {
                MenuBarIcon(busy: backend.busy)
            }
            .labelStyle(.iconOnly)
        }
    }

    private func showMain() {
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}
