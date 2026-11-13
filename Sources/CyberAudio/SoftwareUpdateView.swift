import SwiftUI

struct SoftwareUpdateSection: View {
    @EnvironmentObject private var updater: UpdaterController

    var body: some View {
        SettingsSection("软件更新") {
            LabeledContent("当前版本", value: updater.version)
            SettingsToggle(title: "自动检查更新", isOn: Binding(get: { updater.automaticChecks }, set: { updater.automaticChecks = $0 }))
            SettingsToggle(title: "自动下载更新", isOn: Binding(get: { updater.automaticDownloads }, set: { updater.automaticDownloads = $0 }))
                .disabled(!updater.automaticChecks)
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(updater.displayStatus).font(.callout)
                    if let date = updater.lastCheck {
                        Text("上次检查：\(date.formatted(date: .abbreviated, time: .shortened))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button(updater.menuTitle) { updater.checkForUpdates() }.settingsButton()
                    .disabled(!updater.canPerformAction)
                    .accessibilityIdentifier("software-update-action")
            }
            if updater.downloading {
                if let progress = updater.downloadProgress { ProgressView(value: progress) }
                else { ProgressView("正在后台下载…").controlSize(.small) }
            }
            if updater.gate.waiting { ProgressView().controlSize(.small) }
            if let message = updater.gate.failure ?? updater.errorMessage {
                Text(message).font(.callout).foregroundStyle(.red).textSelection(.enabled)
            }
            Text("每天检查稳定版。运行中确认重启，主动退出时安装已就绪的更新。重启前请复制文字或保存音频；模型与配置会保留。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct UpdateWaitingBar: View {
    @EnvironmentObject private var updater: UpdaterController
    var body: some View {
        HStack(spacing: 12) {
            ProgressView().controlSize(.small)
            Text(updater.displayStatus).font(.callout)
            Spacer()
            Button("取消等待") { updater.gate.cancel() }.studioButton()
        }
        .padding(14).contentSurface()
        .accessibilityElement(children: .contain)
    }
}
