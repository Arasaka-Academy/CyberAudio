import AppKit
import SwiftUI

struct PreparationView: View {
    @ObservedObject var preparation: PreparationController
    @ViewState private var showNetwork = false

    private func bytes(_ value: Int64) -> String { ByteCountFormatter.string(fromByteCount: value, countStyle: .file) }
    private var primaryTitle: String {
        if preparation.snapshot?.models_ready == true { return "重新检查并启动" }
        return preparation.completedBytes > 0 ? "继续下载" : "下载本地模型"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(systemName: "waveform.circle.fill").font(.system(size: 40)).foregroundStyle(StudioStyle.accent)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 5) {
                    Text("准备 CyberAudio").font(.title2.weight(.semibold))
                    Text("首次使用需下载模型，之后可离线使用。")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(spacing: 12) {
                        readiness("语音模型", ready: preparation.snapshot?.models_ready == true,
                                  detail: preparation.snapshot.map { $0.models_ready ? "已验证 · \(bytes($0.total_bytes))" : "共 \(bytes($0.total_bytes)) · 可恢复已完成进度" } ?? "正在检查…")
                        Divider()
                        HStack {
                            readiness("音频转换 · FFmpeg", ready: preparation.snapshot?.ffmpeg_ready == true,
                                      detail: preparation.snapshot?.ffmpeg_ready == true ? "已就绪" : "用于读取录音与导入音频")
                            if preparation.snapshot?.ffmpeg_ready == false {
                                Button("选择…", action: chooseFFmpeg).disabled(preparation.busy)
                            }
                        }
                    }.padding(16).contentSurface()
                    if preparation.snapshot?.ffmpeg_ready == false {
                        HStack(spacing: 12) {
                            Text("尚未安装？可通过 Homebrew 安装 ffmpeg。")
                            Link("安装说明", destination: URL(string: "https://ffmpeg.org/download.html#build-mac")!)
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                    if preparation.busy {
                        VStack(alignment: .leading, spacing: 7) {
                            if preparation.totalBytes > 0 && !preparation.verifying && preparation.phase == .downloading {
                                ProgressView(value: Double(preparation.completedBytes), total: Double(preparation.totalBytes))
                                Text("\(bytes(preparation.completedBytes)) / \(bytes(preparation.totalBytes))")
                                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            } else {
                                HStack { ProgressView().controlSize(.small); Text(preparation.verifying ? "正在验证模型…" : preparation.message).font(.callout) }
                            }
                        }.accessibilityIdentifier("preparation-progress")
                    } else {
                        Text(preparation.message).font(.callout)
                            .foregroundStyle(preparation.phase == .failed ? .orange : .secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("preparation-message")
                    }
                    if let status = preparation.snapshot, !status.models_ready {
                        if let available = status.available_bytes {
                            Text("可用空间 \(bytes(available)) · 下载与验证需约 \(bytes(status.required_bytes))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        HStack(spacing: 12) {
                            Picker("下载源", selection: $preparation.downloadSource) {
                                Text("Hugging Face").tag(PreparationDownloadSource.huggingFace)
                                Text("ModelScope").tag(PreparationDownloadSource.modelScope)
                            }.fixedSize().disabled(preparation.busy)
                                .accessibilityIdentifier("model-download-source")
                            Text("切换后可继续下载").font(.caption).foregroundStyle(.secondary)
                            Spacer(minLength: 0)
                        }
                        DisclosureGroup("网络设置（可选）", isExpanded: $showNetwork) {
                            TextField("http://127.0.0.1:端口 或 socks5://127.0.0.1:端口", text: $preparation.proxy)
                                .textFieldStyle(.roundedBorder).font(.caption)
                                .accessibilityLabel("模型下载代理")
                                .disabled(preparation.busy)
                        }.font(.callout)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.scrollBounceBehavior(.basedOnSize)
            HStack(spacing: 10) {
                if preparation.busy {
                    Button("取消", action: preparation.cancel).keyboardShortcut(.cancelAction)
                        .disabled(preparation.phase == .cancelling)
                } else {
                    Button("重新检查") { preparation.check() }
                    if preparation.snapshot?.invalid_partials.isEmpty == false {
                        Button("重新下载") { preparation.download(restart: true) }
                            .disabled(preparation.snapshot?.can_download != true)
                    }
                }
                Spacer()
                Button(primaryTitle) {
                    if preparation.snapshot?.models_ready == true { preparation.check() }
                    else { preparation.download() }
                }
                .keyboardShortcut(.defaultAction).studioButton(prominent: true)
                .disabled(preparation.busy || preparation.snapshot == nil ||
                          preparation.snapshot?.models_ready == false && preparation.snapshot?.can_download != true ||
                          preparation.snapshot?.invalid_partials.isEmpty == false)
            }
        }
        .padding(24).frame(minWidth: 560, minHeight: 440)
        .background { WorkspaceBackground() }.tint(StudioStyle.accent)
        .accessibilityIdentifier("preparation-view")
    }

    private func readiness(_ title: String, ready: Bool, detail: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: ready ? "checkmark.circle.fill" : "circle.dotted")
                .foregroundStyle(ready ? StudioStyle.accent : .secondary).padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.accessibilityElement(children: .combine)
    }

    private func chooseFFmpeg() {
        let panel = NSOpenPanel()
        panel.title = "选择 FFmpeg 可执行文件"; panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: "/opt/homebrew/bin")
        panel.begin { response in
            if response == .OK, let url = panel.url { preparation.selectFFmpeg(url) }
        }
    }
}
