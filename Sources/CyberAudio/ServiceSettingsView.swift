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
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}

struct ServiceSettingsView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var updater: UpdaterController
    @StateObject private var login = LoginState()

    var body: some View {
        Form {
            Section {
                HStack(spacing: 14) {
                    Image(systemName: "waveform.circle.fill")
                        .font(.system(size: 38)).foregroundStyle(StudioStyle.accent.gradient)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("CyberAudio").font(.title2.weight(.semibold))
                        Text("本地语音工作台").font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    EngineStatus()
                }.padding(.vertical, 6)
            }
            Section("引擎与模型") {
                Text(backend.detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                HStack {
                    if backend.running {
                        Button("停止引擎", systemImage: "stop.circle") { Task { await backend.stop() } }
                            .disabled(audio.cannotStart || backend.status == "stopping")
                    } else {
                        Button("启动引擎", systemImage: "play.circle") { backend.start() }.disabled(audio.preparingUpdate)
                    }
                    Button("重启", systemImage: "arrow.clockwise") { Task { await backend.restart() } }
                        .disabled(audio.cannotStart || backend.status == "stopping")
                }
                model("语音识别", name: "Qwen3-ASR 1.7B · 8-bit", revision: "a8379a2e2f9e")
                model("语音合成", name: "Qwen3-TTS 1.7B · CustomVoice · 8-bit", revision: "41d3337e8b7f")
                if let load = backend.startup["load_ms"], let warm = backend.startup["warmup_ms"] {
                    Text("本次加载 \(load.display) ms · 预热 \(warm.display) ms")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Section {
                LabeledContent("本机 API") {
                    Text(backend.localURL).font(.callout.monospaced()).textSelection(.enabled)
                }
                LabeledContent("Tailscale HTTPS") {
                    Text(backend.httpsURL).font(.caption.monospaced()).textSelection(.enabled)
                        .lineLimit(2).multilineTextAlignment(.trailing)
                }
                LabeledContent("API Token") {
                    Text("••••••••••••").foregroundStyle(.secondary)
                    Button(login.tokenCopied ? "已复制" : "复制 Token", systemImage: login.tokenCopied ? "checkmark" : "doc.on.doc") {
                        backend.copyToken()
                        login.tokenCopied = true
                    }.disabled(backend.configuration == nil)
                }
            } header: {
                Text("API 连接")
            } footer: {
                Text("远程客户端需加入同一 Tailscale 网络，并携带 Bearer Token。")
            }
            Section {
                Toggle("登录时打开 CyberAudio", isOn: Binding(get: { login.enabled }, set: { enabled in
                    do { try login.setEnabled(enabled); login.error = nil }
                    catch { login.error = "登录启动设置失败：\(error.localizedDescription)" }
                }))
                if login.status == .requiresApproval {
                    Label("请在系统设置中批准 CyberAudio 登录项。", systemImage: "exclamationmark.circle")
                        .font(.callout).foregroundStyle(.orange)
                }
                if let loginError = login.error {
                    InlineNotice(message: loginError) { login.error = nil }
                }
                Button("打开系统登录项设置", systemImage: "arrow.up.forward.app") { SMAppService.openSystemSettingsLoginItems() }
            } header: {
                Text("登录启动")
            } footer: {
                Text("App 仅显示在菜单栏。关闭窗口后继续运行；从菜单栏恢复主窗口，或退出 App 以停止 API 并卸载模型。")
            }
            SoftwareUpdateSection()
            Section("数据与日志") {
                HStack {
                    Button("打开数据目录", systemImage: "folder") { NSWorkspace.shared.open(backend.root) }
                    Button("打开日志", systemImage: "doc.text") { NSWorkspace.shared.open(backend.root.appendingPathComponent("logs")) }
                }
                Text("模型与运行环境保存在数据目录，App 直接管理本地推理进程。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Text("CyberAudio \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0") · SwiftUI + MLX Audio")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity)
            }
        }
        .formStyle(.grouped).scrollContentBackground(.hidden)
        .background { WorkspaceBackground() }
        .frame(width: 620, height: 640)
        .tint(StudioStyle.accent)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in login.refresh() }
        .task(id: login.tokenCopied) {
            if login.tokenCopied {
                try? await Task.sleep(for: .seconds(2))
                guard !Task.isCancelled else { return }
                login.tokenCopied = false
            }
        }
    }

    private func model(_ title: String, name: String, revision: String) -> some View {
        LabeledContent(title) {
            VStack(alignment: .trailing, spacing: 4) {
                Text(name).font(.callout)
                Text("revision \(revision)…").font(.caption.monospaced()).foregroundStyle(.secondary)
            }
        }
    }
}
