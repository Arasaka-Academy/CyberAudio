import AppKit
import ServiceManagement
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

    var body: some Scene {
        Window("CyberAudio", id: "main") {
            ContentView().environmentObject(backend).environmentObject(audio)
        }
        .defaultSize(width: 1040, height: 700)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("显示 CyberAudio") { showMain() }.keyboardShortcut("0", modifiers: .command)
            }
        }
        MenuBarExtra("CyberAudio · \(backend.statusLabel)", systemImage: backend.busy ? "waveform" : "waveform.circle") {
            Text("CyberAudio · \(backend.statusLabel)")
            Button("显示主窗口") { showMain() }
            Divider()
            if backend.running {
                Button("停止引擎") { Task { await backend.stop() } }
            } else {
                Button("启动引擎") { backend.start() }
            }
            Button("重启引擎") { Task { await backend.restart() } }
            Divider()
            Button("退出 CyberAudio") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
    }

    private func showMain() {
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}

private enum Workspace: String, CaseIterable, Identifiable {
    case stt = "语音转文字", tts = "文字转语音", service = "服务与设置"
    var id: Self { self }
    var symbol: String { switch self { case .stt: "mic"; case .tts: "speaker.wave.2"; case .service: "slider.horizontal.3" } }
    var subtitle: String { switch self { case .stt: "STT"; case .tts: "TTS"; case .service: "API" } }
}

@MainActor
private final class NavigationState: ObservableObject {
    @Published var selection: Workspace? = .stt
}

@MainActor
private final class LoginState: ObservableObject {
    @Published private(set) var status = SMAppService.mainApp.status
    var enabled: Bool { status == .enabled || status == .requiresApproval }

    func setEnabled(_ enabled: Bool) throws {
        defer { status = SMAppService.mainApp.status }
        if enabled {
            try SMAppService.mainApp.register()
            if SMAppService.mainApp.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
            }
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @StateObject private var navigation = NavigationState()

    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 10) {
                    Image(systemName: "waveform.circle.fill").font(.system(size: 30)).foregroundStyle(.teal)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("CyberAudio").font(.headline)
                        Text("本地语音工作台").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.horizontal, 16).padding(.top, 16)
                List(Workspace.allCases, selection: $navigation.selection) { item in
                    HStack {
                        Label(item.rawValue, systemImage: item.symbol)
                        Spacer()
                        Text(item.subtitle).font(.caption2.monospaced()).foregroundStyle(.secondary)
                    }.padding(.vertical, 6).tag(item)
                }.listStyle(.sidebar)
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Circle().fill(backend.ready ? (backend.busy ? .orange : .teal) : .gray).frame(width: 7, height: 7)
                        Text(backend.statusLabel).font(.callout.weight(.medium))
                        if backend.status == "loading" { ProgressView().controlSize(.small) }
                    }
                    Text("Qwen3 · 1.7B · 8-bit").font(.caption).foregroundStyle(.secondary)
                    Text("关闭窗口后继续运行\n退出 App 后停止 API").font(.caption).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
                }.padding(16)
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 235, max: 280)
        } detail: {
            VStack(spacing: 0) {
                if backend.status == "failed" || backend.status == "loading" {
                    HStack(spacing: 10) {
                        if backend.status == "loading" { ProgressView().controlSize(.small) }
                        else { Image(systemName: "exclamationmark.circle").foregroundStyle(.orange) }
                        Text(backend.detail).font(.callout).lineLimit(3)
                        Spacer()
                        Button("查看服务") { navigation.selection = .service }
                    }.padding(14).background(.quaternary.opacity(0.3))
                }
                if let message = audio.errorMessage {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(message).textSelection(.enabled)
                        Spacer()
                        Button { audio.errorMessage = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("关闭错误提示")
                    }.padding(14).background(.orange.opacity(0.08))
                }
                switch navigation.selection ?? .stt {
                case .stt: STTView()
                case .tts: TTSView()
                case .service: ServiceView()
                }
            }
            .frame(minWidth: 660, minHeight: 580)
        }
        .tint(.teal)
    }
}

private struct WorkspaceHeader: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 28, weight: .semibold))
            Text(subtitle).font(.callout).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct STTView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            WorkspaceHeader(title: "语音转文字", subtitle: "说完一句，再获取完整文字。支持中文、英文与混合语音。")
            HStack {
                Picker("识别语言", selection: $audio.sttLanguage) {
                    Text("自动识别").tag("auto"); Text("中文").tag("zh"); Text("English").tag("en")
                }.frame(width: 230).disabled(audio.working)
                Spacer()
                Text("最长 60 秒 · 20MiB").font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 14) {
                Button {
                    Task { if audio.recording { await audio.stopAndTranscribe(using: backend) } else { await audio.beginRecording() } }
                } label: {
                    Label(audio.recording ? "停止并识别" : "开始录音", systemImage: audio.recording ? "stop.fill" : "mic.fill").frame(minWidth: 110)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .tint(audio.recording ? .red : .teal)
                .disabled(!audio.recording && (!backend.ready || backend.busy || audio.working))
                .keyboardShortcut("r", modifiers: [.command, .shift])
                Button("导入音频…", systemImage: "square.and.arrow.down") { audio.importAudio(using: backend) }
                    .controlSize(.large).disabled(!backend.ready || backend.busy || audio.working)
                Spacer()
                if audio.recording {
                    Text(audio.recordingSeconds >= 59 ? "已到上限，请点停止" : String(format: "%02d:%02d", audio.recordingSeconds / 60, audio.recordingSeconds % 60))
                        .font(.system(.body, design: .monospaced)).foregroundStyle(.red)
                }
                if audio.transcribing { ProgressView().controlSize(.small); Text("识别中…").foregroundStyle(.secondary) }
            }
            Divider()
            HStack {
                Text("识别结果").font(.headline)
                Spacer()
                Button("复制", systemImage: "doc.on.doc") {
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(audio.transcript, forType: .string)
                }.disabled(audio.transcript.isEmpty)
                Button("清空") { audio.transcript = ""; audio.sttTiming = "" }.disabled(audio.transcript.isEmpty || audio.transcribing)
            }
            ZStack(alignment: .topLeading) {
                if audio.transcript.isEmpty { Text("录音或导入文件后，文字将显示在这里。") .foregroundStyle(.tertiary).padding(9).allowsHitTesting(false) }
                TextEditor(text: $audio.transcript).font(.system(size: 17)).scrollContentBackground(.hidden).padding(4)
                    .accessibilityLabel("识别结果")
            }.frame(maxHeight: .infinity).background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Text(audio.sttTiming.isEmpty ? "录音仅临时处理，不写入历史记录。" : audio.sttTiming)
                Spacer()
                Text("Apple GPU").font(.caption.monospaced())
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(30)
    }
}

private struct TTSView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    private let voices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            WorkspaceHeader(title: "文字转语音", subtitle: "选择音色与表达方式，在本机生成语音。")
            HStack {
                Text("朗读文本").font(.headline)
                Spacer()
                Text("\(audio.text.unicodeScalars.count) / 500").font(.caption.monospaced()).foregroundStyle(audio.text.unicodeScalars.count > 500 ? .red : .secondary)
            }
            TextEditor(text: $audio.text).font(.system(size: 17)).scrollContentBackground(.hidden).padding(8)
                .frame(minHeight: 140, maxHeight: .infinity)
                .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityLabel("朗读文本").disabled(audio.generating)
            HStack(spacing: 22) {
                Picker("音色", selection: $audio.voice) { ForEach(voices, id: \.self) { Text($0).tag($0) } }.frame(maxWidth: .infinity)
                Picker("语言", selection: $audio.ttsLanguage) {
                    Text("自动").tag("auto"); Text("中文").tag("chinese"); Text("English").tag("english")
                }.frame(maxWidth: .infinity)
            }.disabled(audio.generating)
            VStack(alignment: .leading, spacing: 8) {
                Text("表达指令").font(.callout.weight(.medium))
                TextField("例如：自然、清晰、亲切地朗读。", text: $audio.instructions).textFieldStyle(.roundedBorder).disabled(audio.generating)
                Text("预设音色支持语气指令；本版本不提供声音克隆。").font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            HStack(spacing: 14) {
                if audio.generating {
                    Button("取消生成", systemImage: "stop.fill") { audio.cancelGeneration() }.controlSize(.large)
                    ProgressView().controlSize(.small)
                } else {
                    Button("生成并播放", systemImage: "play.fill") { audio.generate(using: backend) }
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .disabled(!backend.ready || backend.busy || audio.working || audio.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || audio.text.unicodeScalars.count > 500 || audio.instructions.unicodeScalars.count > 200)
                }
                Toggle("流式播放", isOn: $audio.streaming).disabled(audio.generating).toggleStyle(.checkbox)
                Spacer()
                if audio.playing {
                    Button("停止播放", systemImage: "stop.circle") { if audio.generating { audio.cancelGeneration() } else { audio.stopPlayback() } }
                } else {
                    Button("重播", systemImage: "arrow.counterclockwise") { do { try audio.playGenerated() } catch { audio.errorMessage = error.localizedDescription } }
                        .disabled(audio.generated.isEmpty || audio.generating)
                }
                Button("保存 WAV…", systemImage: "square.and.arrow.up") { audio.save() }.disabled(audio.generated.isEmpty || audio.generating)
            }
            Text(audio.ttsTiming.isEmpty ? "24kHz · 单声道 · PCM 16-bit。完整生成后可保存为 WAV。" : audio.ttsTiming)
                .font(.caption).foregroundStyle(.secondary).frame(height: 20, alignment: .leading)
        }.padding(30)
    }
}

private struct ServiceView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @StateObject private var login = LoginState()
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                WorkspaceHeader(title: "服务与设置", subtitle: "一个 App，统一提供语音识别与语音合成。")
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Label(backend.statusLabel, systemImage: backend.ready ? "checkmark.circle.fill" : "circle.dotted").font(.headline).foregroundStyle(backend.ready ? .teal : .secondary)
                        Text(backend.detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    Spacer()
                    if backend.running {
                        Button("停止引擎") { Task { await backend.stop() } }.disabled(audio.working)
                    } else {
                        Button("启动引擎") { backend.start() }
                    }
                    Button("重启") { Task { await backend.restart() } }.disabled(audio.working || backend.status == "stopping")
                }
                Divider()
                LabeledContent("本机 API") { Text(backend.localURL).textSelection(.enabled).font(.body.monospaced()) }
                LabeledContent("Tailscale HTTPS") { Text(backend.httpsURL).textSelection(.enabled).font(.callout.monospaced()) }
                LabeledContent("API Token") { Text("••••••••••••••••").foregroundStyle(.secondary); Button("复制 Token") { backend.copyToken() } }
                Text("远程客户端需要加入同一 Tailscale 网络，并携带 Bearer Token。").font(.caption).foregroundStyle(.secondary)
                Divider()
                model("语音识别", name: "Qwen3-ASR 1.7B · 8-bit", revision: "a8379a2e2f9e")
                model("语音合成", name: "Qwen3-TTS 1.7B · CustomVoice · 8-bit", revision: "41d3337e8b7f")
                if let load = backend.startup["load_ms"], let warm = backend.startup["warmup_ms"] {
                    Text("本次模型加载 \(load.display) ms · 预热 \(warm.display) ms").font(.caption.monospaced()).foregroundStyle(.secondary)
                }
                Divider()
                Toggle("登录时打开 CyberAudio", isOn: Binding(get: { login.enabled }, set: { enabled in
                    do { try login.setEnabled(enabled) }
                    catch { audio.errorMessage = "登录启动设置失败：\(error.localizedDescription)" }
                }))
                if login.status == .requiresApproval {
                    Text("请在系统设置中批准 CyberAudio 登录项。").font(.callout).foregroundStyle(.orange)
                }
                Text("关闭窗口后，App 会继续在菜单栏运行。选择“退出 CyberAudio”会停止 API 并卸载两个模型。登录启动在用户登录后生效。").font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button("打开数据目录", systemImage: "folder") { NSWorkspace.shared.open(backend.root) }
                    Button("打开日志", systemImage: "doc.text") { NSWorkspace.shared.open(backend.root.appendingPathComponent("logs")) }
                    Button("登录项设置") { SMAppService.openSystemSettingsLoginItems() }
                }
                Text("CyberAudio 0.1.0 · SwiftUI + MLX Audio\nApp 直接管理本地 Python 推理进程。模型与运行环境保存在数据目录。").font(.caption).foregroundStyle(.tertiary)
            }.padding(30)
        }
    }
    private func model(_ title: String, name: String, revision: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).frame(width: 90, alignment: .leading).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 5) {
                Text(name).font(.callout.weight(.medium))
                Text("revision \(revision)…").font(.caption.monospaced()).foregroundStyle(.tertiary)
            }
            Spacer()
        }
    }
}
