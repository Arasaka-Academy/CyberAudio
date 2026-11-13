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

    private let pages: [(id: Int, title: String, icon: String)] = [
        (0, "听写", "mic"), (3, "模型", "square.stack.3d.up"),
        (1, "服务", "network"), (2, "通用", "gearshape")
    ]

    init(initialTab: Int = 0) { _tab = ViewState(wrappedValue: initialTab) }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            Group {
                switch tab {
                case 3: ModelSettingsView()
                case 1: settingsScroll { serviceSettings }
                case 2: settingsScroll { generalSettings }
                default: settingsScroll { dictationSettings }
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).id(tab)
        }
        .frame(width: 620, height: 560)
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

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 5) {
                Image(systemName: "waveform").font(.system(size: 23, weight: .medium)).foregroundStyle(StudioStyle.accent)
                    .padding(.bottom, 5).accessibilityHidden(true)
                Text("CyberAudio").font(.system(size: 14, weight: .semibold))
                Text("偏好设置").font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 10)
            VStack(spacing: 5) {
                ForEach(pages, id: \.id) { page in
                    Button { tab = page.id } label: {
                        HStack(spacing: 10) {
                            Image(systemName: page.icon).font(.system(size: 14)).frame(width: 18)
                            Text(page.title).font(.callout.weight(tab == page.id ? .semibold : .regular))
                            Spacer(minLength: 0)
                        }.padding(.horizontal, 10).frame(height: 36).contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(tab == page.id ? Color.primary : .secondary)
                    .background(tab == page.id ? StudioStyle.accent.opacity(0.16) : .clear, in: .rect(cornerRadius: 8))
                    .accessibilityAddTraits(tab == page.id ? .isSelected : [])
                    .accessibilityIdentifier("settings-page-\(page.id)")
                }
            }
            Spacer(minLength: 0)
            VStack(alignment: .leading, spacing: 5) {
                Text("本地运行 · 隐私优先")
                Text("版本 \(updater.version)").monospacedDigit()
            }.font(.system(size: 10)).foregroundStyle(.secondary).padding(.horizontal, 10)
        }
        .padding(.horizontal, 12).padding(.vertical, 24).frame(width: 140)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.35))
        .accessibilityElement(children: .contain).accessibilityLabel("设置导航")
        .onMoveCommand { direction in
            guard let index = pages.firstIndex(where: { $0.id == tab }) else { return }
            if direction == .up { tab = pages[max(0, index - 1)].id }
            if direction == .down { tab = pages[min(pages.count - 1, index + 1)].id }
        }
    }

    private func settingsScroll<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        ScrollView {
            content().frame(maxWidth: .infinity, alignment: .leading).padding(20)
        }.scrollBounceBehavior(.basedOnSize)
    }

    private var dictationSettings: some View {
        VStack(alignment: .leading, spacing: 20) {
            SettingsPageHeader(title: "随处听写", detail: "按住快捷键说话，松开后将文字写入当前输入框。")
            SettingsSection("快捷听写") {
                SettingsToggle(title: "启用快捷听写", isOn: Binding(get: { dictation.enabled }, set: { dictation.setEnabled($0) }))
                Text(dictation.permissionSummary).font(.caption).foregroundStyle(.secondary)
                Divider()
                HStack(spacing: 10) {
                    Text("长按录音").font(.callout)
                    Spacer()
                    Text(dictation.shortcut.display).font(.system(.callout, design: .monospaced).weight(.medium))
                        .padding(.horizontal, 10).padding(.vertical, 5).background(.quaternary, in: .rect(cornerRadius: 6))
                    Button("修改…") { shortcutError = ""; dictation.configureShortcut(true); recordingShortcut = true }.settingsButton()
                }
                if dictation.shortcut != .rightOption {
                    Button("恢复默认快捷键") { dictation.setShortcut(.rightOption) }.buttonStyle(.link)
                }
            }
            SettingsSection("使用权限") {
                VStack(spacing: 0) {
                    permission("麦克风", detail: "录制要识别的语音", granted: dictation.microphoneGranted) {
                        Task { await dictation.requestMicrophone() }
                        dictation.openPrivacy("Microphone")
                    }
                    Divider()
                    permission("辅助功能", detail: "在原输入框写入识别结果", granted: dictation.accessibilityGranted) { dictation.requestAccessibility() }
                    Divider()
                    permission("输入监控", detail: "在其他应用中识别听写快捷键", granted: dictation.inputMonitoringGranted) { dictation.requestInputMonitoring() }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Label("松开结束 · Esc 取消", systemImage: "keyboard")
                Text("识别时切换应用或输入框，结果会保留在主窗口，供你复制。不会自动回车或发送消息。")
                Text("关闭主窗口后仍可听写；右 Option 配合其他按键时保留正常输入。")
            }.font(.caption).foregroundStyle(.secondary)
        }
    }

    private func permission(_ title: String, detail: String, granted: Bool, action: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Image(systemName: granted ? "checkmark.circle.fill" : "circle.dotted").foregroundStyle(granted ? StudioStyle.accent : .secondary)
            VStack(alignment: .leading, spacing: 2) { Text(title).font(.callout); Text(detail).font(.caption).foregroundStyle(.secondary) }
            Spacer()
            if granted { Text("已开启").font(.caption).foregroundStyle(.secondary) }
            else { Button("去开启", action: action).settingsButton() }
        }.padding(.vertical, 8)
    }

    private var serviceSettings: some View {
        VStack(alignment: .leading, spacing: 20) {
            SettingsPageHeader(title: "语音服务", detail: "在本机处理语音，也可为同一 Tailscale 网络中的应用提供 API。")
            SettingsSection("本地引擎") {
                HStack {
                    EngineStatus()
                    Spacer(minLength: 0)
                    if backend.running {
                        Button("停止") { Task { await backend.stop() } }.settingsButton().disabled(audio.cannotStart || dictation.blocksUpdate || backend.status == "stopping")
                    } else {
                        Button("启动") { backend.start() }.settingsButton().disabled(audio.preparingUpdate || audio.managingModels || dictation.blocksUpdate)
                    }
                    Button("重启") { Task { await backend.restart() } }.settingsButton().disabled(audio.cannotStart || dictation.blocksUpdate || backend.status == "stopping")
                }
                Text(backend.detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
            SettingsSection("API 连接") {
                VStack(alignment: .leading, spacing: 14) {
                    serviceRow("本机") {
                        Text(backend.localURL).font(.caption.monospaced()).textSelection(.enabled)
                    }
                    serviceRow("Tailscale") {
                        if backend.httpsURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || backend.configuration == nil {
                            Text("未配置").foregroundStyle(.secondary)
                        } else {
                            Text(backend.httpsURL).font(.caption.monospaced()).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    serviceRow("API Token") {
                        HStack {
                            Text("••••••••••••").foregroundStyle(.secondary)
                            Spacer(minLength: 12)
                            Button(login.tokenCopied ? "已复制" : "复制 Token") { backend.copyToken(); login.tokenCopied = true }.settingsButton()
                                .disabled(backend.configuration == nil)
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            Text("远程客户端需加入同一 Tailscale 网络，并携带 Bearer Token。").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("数据目录", systemImage: "folder") { NSWorkspace.shared.open(backend.root) }.settingsButton()
                Button("日志", systemImage: "doc.text") { NSWorkspace.shared.open(backend.root.appendingPathComponent("logs")) }.settingsButton()
            }
        }
    }

    private var generalSettings: some View {
        VStack(alignment: .leading, spacing: 20) {
            SettingsPageHeader(title: "通用", detail: "管理登录启动与软件更新。")
            SettingsSection("登录启动") {
                SettingsToggle(title: "登录时打开 CyberAudio", isOn: Binding(get: { login.enabled }, set: { enabled in
                    do { try login.setEnabled(enabled); login.error = nil }
                    catch { login.error = "登录启动设置失败：\(error.localizedDescription)" }
                }))
                if login.status == .requiresApproval { Text("请在系统设置中批准登录项。").font(.caption).foregroundStyle(.orange) }
                if let error = login.error { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2) }
                Button("打开系统登录项设置") { SMAppService.openSystemSettingsLoginItems() }.settingsButton()
            }
            SoftwareUpdateSection()
        }
    }
    private func serviceRow<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title).font(.callout).foregroundStyle(.secondary).frame(width: 62, alignment: .leading)
            content().frame(maxWidth: .infinity, alignment: .leading)
        }.font(.callout)
    }

}
