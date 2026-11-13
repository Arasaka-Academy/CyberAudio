import AppKit
import SwiftUI

struct ModelSettingsView: View {
    @EnvironmentObject private var models: ModelManagerController
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var preparation: PreparationController
    @ViewState private var showNetwork = false
    @ViewState private var deleting: ManagedModel? = nil
    private func bytes(_ value: Int64) -> String { ByteCountFormatter.string(fromByteCount:value,countStyle:.file) }
    private var firstRunDownload: Bool { preparation.busy && preparation.phase == .downloading }

    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:20) {
                HStack(alignment: .top, spacing: 12) {
                    SettingsPageHeader(title: "本地模型", detail: "识别与合成分别选择，下载完成后手动启用。")
                    Button { models.refresh() } label: { Image(systemName: "arrow.clockwise") }
                        .settingsButton().disabled(!models.canManage).help("重新检查模型")
                        .accessibilityLabel("重新检查模型")
                }
                if models.busy || firstRunDownload {
                    VStack(alignment:.leading,spacing:7) {
                        HStack(spacing:8) {
                            ProgressView().controlSize(.small)
                            Text(preparation.verifying ? "正在校验模型…" : (firstRunDownload ? "正在下载当前模型…" : models.message.isEmpty ? "正在检查模型…" : models.message))
                                .font(.callout).fixedSize(horizontal:false,vertical:true)
                            Spacer(minLength:4)
                            if models.cancellable || firstRunDownload {
                                Button("取消") { if firstRunDownload { preparation.cancel() } else { models.cancel() } }
                            }
                        }
                        if preparation.totalBytes > 0,!preparation.verifying {
                            let completed = min(preparation.completedBytes,preparation.totalBytes)
                            ProgressView(value:Double(completed),total:Double(preparation.totalBytes))
                            HStack {
                                Text("\(bytes(completed)) / \(bytes(preparation.totalBytes))")
                                Spacer()
                                Text("\(Int(Double(completed)/Double(preparation.totalBytes)*100))%")
                            }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            if !preparation.currentFile.isEmpty {
                                Text(preparation.currentFile).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                            }
                        }
                    }.padding(12).contentSurface().accessibilityIdentifier("model-management-progress")
                }
                if let error = models.error {
                    InlineNotice(message: error)
                        .accessibilityIdentifier("model-management-error")
                } else if !models.busy,!models.message.isEmpty {
                    Text(models.message).font(.caption).foregroundStyle(.secondary)
                }
                if let snapshot = models.snapshot {
                    modelGroup("语音识别 · Qwen3-ASR",component:"asr",snapshot:snapshot)
                    modelGroup("语音合成 · Qwen3-TTS",component:"tts",snapshot:snapshot)
                    SettingsSection("模型存储") {
                        VStack(alignment:.leading,spacing:10) {
                            Text(snapshot.models_directory).font(.caption.monospaced()).textSelection(.enabled)
                                .fixedSize(horizontal:false,vertical:true).accessibilityIdentifier("models-directory")
                            VStack(alignment: .leading, spacing: 4) {
                                Text("模型占用 \(bytes(snapshot.occupied_bytes))")
                                if let available = snapshot.available_bytes { Text("可用 \(bytes(available))") }
                            }.font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Button("在 Finder 中打开",systemImage:"folder") {
                                    NSWorkspace.shared.open(URL(fileURLWithPath:snapshot.models_directory,isDirectory:true))
                                }.settingsButton()
                                Button("更改位置…",action:chooseDirectory).settingsButton()
                                    .disabled(!models.canManage && models.operation != .downloading)
                            }
                            Text("支持外置磁盘。更改位置会暂停下载并迁移已下载模型，完成后继续下载。")
                                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                            Text("仅迁移已登记模型与下载暂存文件，目录中的其他文件会保留。")
                                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                        }.frame(maxWidth:.infinity,alignment:.leading)
                    }
                } else if !models.busy {
                    Text("完成首次资源检查后即可管理模型。").font(.callout).foregroundStyle(.secondary)
                }
                SettingsSection("下载设置") {
                    VStack(alignment:.leading,spacing:10) {
                        Picker("下载源",selection:Binding(get: { models.downloadSource },set: { models.downloadSource = $0 })) {
                            Text("Hugging Face").tag(PreparationDownloadSource.huggingFace)
                            Text("ModelScope").tag(PreparationDownloadSource.modelScope)
                        }.disabled(models.busy || preparation.busy)
                        DisclosureGroup("网络设置（可选）",isExpanded:$showNetwork) {
                            TextField("http://主机:端口 或 socks5://主机:端口",text:Binding(get: { preparation.proxy },set: { preparation.proxy = $0 }))
                                .textFieldStyle(.roundedBorder).font(.caption).disabled(models.busy || preparation.busy)
                                .accessibilityLabel("模型下载代理")
                        }
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }
            }.padding(20).frame(maxWidth:.infinity,alignment:.leading)
        }
        .scrollBounceBehavior(.basedOnSize)
        .task { if models.snapshot == nil { models.refresh() } }
        .alert("删除已下载模型？",isPresented:Binding(get: { deleting != nil },set: { if !$0 { deleting = nil } }),presenting:deleting) { model in
            Button("删除",role:.destructive) { models.delete(model); deleting = nil }
            Button("取消",role:.cancel) { deleting = nil }
        } message: { model in
            Text("删除 \(model.name) \(model.parameters) · \(model.quantization)，需要时可重新下载。")
        }
    }

    private func modelGroup(_ title: String, component: String, snapshot: ManagedModelsSnapshot) -> some View {
        SettingsSection(title) {
            VStack(spacing:0) {
                let items = snapshot.models.filter { $0.component == component }
                ForEach(Array(items.enumerated()),id:\.element.id) { index,model in
                    if index > 0 { Divider() }
                    modelRow(model)
                }
            }
        }
    }

    private func modelRow(_ model: ManagedModel) -> some View {
        HStack(alignment:.top,spacing:12) {
            VStack(alignment:.leading,spacing:4) {
                HStack(spacing:8) {
                    Text("\(model.parameters) · \(model.quantization.replacingOccurrences(of:"bit",with:"-bit"))").font(.callout.weight(.medium))
                    if model.active {
                        Text(backend.ready ? "使用中" : "当前选择").font(.system(size: 10, weight: .medium))
                            .foregroundStyle(StudioStyle.accent).padding(.horizontal, 6).padding(.vertical, 3)
                            .background(StudioStyle.accent.opacity(0.1), in: Capsule())
                    }
                }
                Text("\(bytes(model.total_bytes)) · \(status(model))").font(.caption).foregroundStyle(.secondary)
                if model.component == "tts",!model.supports_instructions {
                    Text("9 种音色 · 不支持表达指令").font(.caption).foregroundStyle(.secondary)
                }
                if let error = models.modelErrors[model.id] {
                    Text(error).font(.caption).foregroundStyle(.orange).fixedSize(horizontal:false,vertical:true)
                }
                if let problem = model.problem {
                    Text(problem).font(.caption).foregroundStyle(.orange).fixedSize(horizontal:false,vertical:true)
                }
                if model.state != "ready",!model.sources.contains(models.downloadSource.rawValue) {
                    Text("此型号请使用 ModelScope 下载源。").font(.caption).foregroundStyle(.secondary)
                }
            }.frame(maxWidth:.infinity,alignment:.leading)
            HStack(spacing:8) {
                if models.queuedModelIDs.contains(model.id) {
                    Button("取消排队") { models.removeQueued(model.id) }
                } else if model.state == "ready" {
                    if !model.active { Button("设为使用") { models.activate(model) }.disabled(!models.canManage) }
                } else if models.currentModelID != model.id {
                    Button(model.invalid_partials.isEmpty ? (models.modelErrors[model.id] != nil ? "重试" : model.state == "partial" ? "继续下载" : "下载") : "重新下载") {
                        models.download(model,restart:!model.invalid_partials.isEmpty)
                    }.disabled((!models.canManage && models.operation != .downloading) || !model.sources.contains(models.downloadSource.rawValue) || model.state == "unavailable" || model.state == "invalid" && model.invalid_partials.isEmpty)
                }
                if !model.active,model.installed_bytes > 0 || model.resumable_bytes > 0 {
                    Button(role:.destructive) { deleting = model } label: { Image(systemName:"trash") }
                        .buttonStyle(.borderless).disabled(!models.canManage).help("删除未使用模型")
                        .accessibilityLabel("删除 \(model.parameters) \(model.quantization) 模型")
                }
            }.settingsButton()
        }.padding(.vertical,6).accessibilityIdentifier("model-\(model.id)")
    }

    private func status(_ model: ManagedModel) -> String {
        if models.currentModelID == model.id {
            if models.operation == .downloading { return preparation.verifying ? "校验中" : "下载中" }
            if models.operation == .activating { return "切换中" }
            if models.operation == .deleting { return "删除中" }
        }
        if models.queuedModelIDs.contains(model.id) { return "等待下载" }
        return ["ready":"已下载并校验","partial":"可继续下载","missing":"未下载","invalid":"校验失败，请检查目录","unavailable":"模型磁盘未连接" ][model.state] ?? model.state
    }

    private func chooseDirectory() {
        let panel = NSOpenPanel()
        panel.title = "选择模型存储目录"; panel.prompt = "迁移到此目录"
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false; panel.canCreateDirectories = true
        panel.directoryURL = models.snapshot.map { URL(fileURLWithPath:$0.models_directory,isDirectory:true) }
        panel.begin { response in
            if response == .OK,let url = panel.url { models.move(to:url) }
        }
    }
}
