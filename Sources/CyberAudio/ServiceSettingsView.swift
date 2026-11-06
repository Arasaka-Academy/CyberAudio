import AppKit
import ServiceManagement
import SwiftUI

@MainActor
private final class LoginState: ObservableObject {
    @Published private(set) var status = SMAppService.mainApp.status
    @Published var error: String?
    @Published var tokenCopied = false
    var enabled: Bool { status == .enabled || status == .requiresApproval }
    func refresh() { status = SMAppService.mainApp.status }
    func setEnabled(_ enabled: Bool) throws {
        defer { refresh() }
        if enabled {
            try SMAppService.mainApp.register()
            if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        } else { try SMAppService.mainApp.unregister() }
    }
}

struct ServiceSettingsView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var updater: UpdaterController
    @EnvironmentObject private var dictation: DictationController
    @StateObject private var login = LoginState()
    @ViewState private var recordingShortcut: Bool = false
    @ViewState private var shortcutError: String = ""
    @ViewState private var tab: Int = 0

    init(initialTab: Int = 0) { _tab = ViewState(wrappedValue: initialTab) }

    var body: some View {
        TabView(selection: $tab) {
            dictationSettings.tabItem { Label("听写", systemImage: "mic") }.tag(0)
            serviceSettings.tabItem { Label("服务", systemImage: "waveform") }.tag(1)
            generalSettings.tabItem { Label("通用与更新", systemImage: "gearshape") }.tag(2)
        }
        .padding(16).frame(width: 620, height: 560)
        .background { WorkspaceBackground() }.tint(StudioStyle.accent)
        .sheet(isPresented: $recordingShortcut, onDismiss: { dictation.configureShortcut(false) }) {
            VStack(spacing: 16) {
                Text("设置听写快捷键").font(.headline)
                Text(shortcutError.isEmpty ? "按下组合键，或单独按右 Option。" : shortcutError)
                    .font(.callout).foregroundStyle(shortcutError.isEmpty ? Color.secondary : .red)
                ShortcutRecorder(capture: { value in dictation.setShortcut(value); recordingShortcut = false },
                                 cancel: { recordingShortcut = false }, error: { shortcutError = $0 })
                    .frame(width: 320, height: 54)
                    .background(.quaternary, in: .rect(cornerRadius: 12))
                    .overlay { Text("等待按键…").foregroundStyle(.secondary).allowsHitTesting(false) }
                Button("取消") { recordingShortcut = false }.keyboardShortcut(.cancelAction)
            }.padding(24).frame(width: 400)
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in login.refresh() }
        .task(id: login.tokenCopied) {
            if login.tokenCopied { try? await Task.sleep(for: .seconds(2)); if !Task.isCancelled { login.tokenCopied = false } }
        }
    }

    private var dictationSettings: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("随处听写").font(.title3.weight(.semibold))
                    Text(dictation.permissionSummary).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("启用快捷听写", isOn: Binding(get: { dictation.enabled }, set: { dictation.setEnabled($0) }))
                    .toggleStyle(.switch).labelsHidden().accessibilityLabel("启用快捷听写")
            }
            GroupBox {
                HStack(spacing: 12) {
                    Text("长按录音").font(.callout)
                    Spacer()
                    Text(dictation.shortcut.display).font(.system(.callout, design: .monospaced).weight(.medium))
                        .padding(.horizontal, 12).padding(.vertical, 6).background(.quaternary, in: .rect(cornerRadius: 7))
                    Button("修改…") { shortcutError = ""; dictation.configureShortcut(true); recordingShortcut = true }
                    Button("恢复默认") { dictation.setShortcut(.rightOption) }.disabled(dictation.shortcut == .rightOption)
                }.padding(8)
            }
            GroupBox("使用权限") {
                VStack(spacing: 0) {
                    permission("麦克风", detail: "录制要识别的语音", granted: dictation.microphoneGranted) {
                        Task { await dictation.requestMicrophone() }
                        dictation.openPrivacy("Microphone")
                    }
                    Divider()
                    permission("辅助功能", detail: "在原输入框写入识别结果", granted: dictation.accessibilityGranted) { dictation.requestAccessibility() }
                    Divider()
                    permission("输入监控", detail: "在其他应用中识别听写快捷键", granted: dictation.inputMonitoringGranted) { dictation.requestInputMonitoring() }
                }.padding(.horizontal, 8)
            }
            VStack(alignment: .leading, spacing: 8) {
                Label("松开结束 · Esc 取消", systemImage: "keyboard")
                Text("识别时切换应用或输入框，结果会保留在主窗口，供你复制。不会自动回车或发送消息。")
                Text("关闭主窗口后仍可听写；右 Option 配合其他按键时保留正常输入。")
            }.font(.caption).foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }.padding(16)
    }

    private func permission(_ title: String, detail: String, granted: Bool, action: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Image(systemName: granted ? "checkmark.circle.fill" : "circle.dotted").foregroundStyle(granted ? StudioStyle.accent : .secondary)
            VStack(alignment: .leading, spacing: 2) { Text(title).font(.callout); Text(detail).font(.caption).foregroundStyle(.secondary) }
            Spacer()
            Button(granted ? "已开启" : "去开启", action: action).disabled(granted)
        }.padding(.vertical, 10)
    }

    private var serviceSettings: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("本地语音引擎").font(.title3.weight(.semibold)); Spacer(); EngineStatus()
                if backend.running {
                    Button("停止") { Task { await backend.stop() } }.disabled(audio.cannotStart || dictation.blocksUpdate || backend.status == "stopping")
                } else { Button("启动") { backend.start() }.disabled(audio.preparingUpdate || dictation.blocksUpdate) }
                Button("重启") { Task { await backend.restart() } }.disabled(audio.cannotStart || dictation.blocksUpdate || backend.status == "stopping")
            }
            Text(backend.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2).textSelection(.enabled)
            GroupBox("模型") {
                VStack(spacing: 12) {
                    model("语音识别", name: "Qwen3-ASR 1.7B · 8-bit", revision: "a8379a2e2f9e")
                    model("语音合成", name: "Qwen3-TTS 1.7B · CustomVoice · 8-bit", revision: "41d3337e8b7f")
                }.padding(8)
            }
            GroupBox("API 连接") {
                VStack(spacing: 12) {
                    LabeledContent("本机") { Text(backend.localURL).font(.caption.monospaced()).textSelection(.enabled) }
                    LabeledContent("Tailscale") { Text(backend.httpsURL).font(.caption.monospaced()).textSelection(.enabled).lineLimit(1) }
                    LabeledContent("API Token") {
                        Text("••••••••••••").foregroundStyle(.secondary)
                        Button(login.tokenCopied ? "已复制" : "复制 Token") { backend.copyToken(); login.tokenCopied = true }.disabled(backend.configuration == nil)
                    }
                }.font(.callout).padding(8)
            }
            Text("远程客户端需加入同一 Tailscale 网络，并携带 Bearer Token。").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("数据目录", systemImage: "folder") { NSWorkspace.shared.open(backend.root) }
                Button("日志", systemImage: "doc.text") { NSWorkspace.shared.open(backend.root.appendingPathComponent("logs")) }
            }
            Spacer(minLength: 0)
        }.padding(16)
    }

    private var generalSettings: some View {
        Form {
            Section("登录启动") {
                Toggle("登录时打开 CyberAudio", isOn: Binding(get: { login.enabled }, set: { enabled in
                    do { try login.setEnabled(enabled); login.error = nil }
                    catch { login.error = "登录启动设置失败：\(error.localizedDescription)" }
                }))
                if login.status == .requiresApproval { Text("请在系统设置中批准登录项。").font(.caption).foregroundStyle(.orange) }
                if let error = login.error { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2) }
                Button("打开系统登录项设置") { SMAppService.openSystemSettingsLoginItems() }
            }
            SoftwareUpdateSection()
            Text("CyberAudio \(updater.version) · Swift + MLX Audio").font(.caption).foregroundStyle(.secondary)
        }.formStyle(.grouped).scrollContentBackground(.hidden).scrollIndicators(.hidden)
    }
    private func model(_ title: String, name: String, revision: String) -> some View {
        LabeledContent(title) {
            VStack(alignment: .trailing, spacing: 3) {
                Text(name).font(.callout)
                Text("revision \(revision)…").font(.caption.monospaced()).foregroundStyle(.secondary)
            }
        }
    }
}
