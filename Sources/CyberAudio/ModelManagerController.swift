import Combine
import Foundation

struct ManagedModel: Decodable, Identifiable {
    let id: String
    let component: String
    let name: String
    let parameters: String
    let quantization: String
    let repository: String
    let revision: String
    let supports_instructions: Bool
    let total_bytes: Int64
    let sources: [String]
    let directory: String
    let installed_bytes: Int64
    let verified_bytes: Int64
    let resumable_bytes: Int64
    let state: String
    let active: Bool
    let invalid_partials: [String]
    let problem: String?
}

struct ManagedModelsSnapshot: Decodable {
    let models: [ManagedModel]
    let asr_model_id: String
    let tts_model_id: String
    let models_directory: String
    let occupied_bytes: Int64
    let available_bytes: Int64?
    let pending_change: Bool
}

@MainActor
final class ModelManagerController: ObservableObject {
    enum Operation { case checking, downloading, activating, moving, deleting }
    @Published private(set) var snapshot: ManagedModelsSnapshot?
    @Published private(set) var operation: Operation?
    @Published private(set) var currentModelID: String?
    @Published private(set) var queuedModelIDs: [String] = []
    @Published private(set) var message = ""
    @Published private(set) var error: String?
    @Published private(set) var modelErrors: [String:String] = [:]
    @Published private(set) var cancellable = false
    private let backend: BackendController
    private let audio: AudioWorkspace
    private let dictation: DictationController
    let preparation: PreparationController
    private let defaults: UserDefaults
    private var task: Task<Void,Never>?
    private var subscriptions = Set<AnyCancellable>()
    private var pendingMove: URL?
    private var stopped = false
    var busy: Bool { operation != nil }
    var maintenanceBusy: Bool { operation == .activating || operation == .moving }
    var canManage: Bool { !busy && !preparation.busy && !audio.preparingUpdate && !stopped && snapshot?.pending_change != true }
    var downloadSource: PreparationDownloadSource {
        get { preparation.downloadSource }
        set { preparation.downloadSource = newValue; defaults.set(newValue.rawValue,forKey:"ModelDownloadSource"); objectWillChange.send() }
    }

    init(backend: BackendController, audio: AudioWorkspace, dictation: DictationController,
         preparation: PreparationController, defaults: UserDefaults = .standard) {
        self.backend = backend; self.audio = audio; self.dictation = dictation
        self.preparation = preparation; self.defaults = defaults
        if let raw = defaults.string(forKey:"ModelDownloadSource"),let source = PreparationDownloadSource(rawValue:raw) {
            preparation.downloadSource = source
        }
        preparation.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }.store(in:&subscriptions)
        preparation.$downloadSource.dropFirst().sink { [weak self] source in
            self?.defaults.set(source.rawValue,forKey:"ModelDownloadSource")
        }.store(in:&subscriptions)
        preparation.$busy.dropFirst().sink { [weak self] busy in
            guard !busy,self?.preparation.managing == false else { return }
            Task { @MainActor [weak self] in
                guard let self,!self.stopped,self.task == nil else { return }
                if !self.queuedModelIDs.isEmpty { self.nextDownload() }
                else { self.refresh() }
            }
        }.store(in:&subscriptions)
    }

    func refresh() {
        guard !busy,!preparation.busy,!stopped else { return }
        start(.checking) { owner in try await owner.loadStatus() }
    }

    private func loadStatus() async throws {
        guard !stopped else { throw CancellationError() }
        guard let data = try await preparation.manage("models") else { throw CyberAudioError.message("无法读取模型状态，请重新检查。") }
        snapshot = try JSONDecoder().decode(ManagedModelsSnapshot.self,from:data)
        try backend.reloadConfiguration(supportsInstructions:snapshot?.models.first(where: { $0.id == snapshot?.tts_model_id })?.supports_instructions)
    }

    func download(_ model: ManagedModel, restart: Bool = false) {
        guard !audio.preparingUpdate,!stopped,model.sources.contains(downloadSource.rawValue) else { return }
        if operation == .downloading {
            if model.id != currentModelID,!queuedModelIDs.contains(model.id) { queuedModelIDs.append(model.id) }
            return
        }
        guard canManage else { return }
        start(.downloading,modelID:model.id) { owner in
            owner.modelErrors[model.id] = nil
            owner.message = "正在下载 \(model.name) \(model.parameters) · \(model.quantization)…"
            var options = ["--model",model.id,"--source",owner.downloadSource.rawValue,"--restart",restart ? "true" : "false"]
            let proxy = owner.preparation.proxy.trimmingCharacters(in:.whitespacesAndNewlines)
            if !proxy.isEmpty { options += ["--proxy",proxy] }
            _ = try await owner.preparation.manage("download",options:options)
            owner.message = "下载与校验完成，可手动设为使用。"
            try await owner.loadStatus()
        }
    }

    private func nextDownload() {
        guard canManage,!queuedModelIDs.isEmpty else { return }
        let id = queuedModelIDs.removeFirst()
        if let model = snapshot?.models.first(where: { $0.id == id }) { download(model) }
    }
    func removeQueued(_ id: String) { queuedModelIDs.removeAll { $0 == id } }

    func activate(_ model: ManagedModel) {
        guard canManage,model.state == "ready",!model.active else { return }
        start(.activating,modelID:model.id) { owner in
            _ = try await owner.preparation.manage("verify",options:["--model",model.id])
            try await owner.change(command:"select-model",options:["--model",model.id])
            owner.message = "已启用 \(model.name) \(model.parameters) · \(model.quantization)。"
            try await owner.loadStatus()
        }
    }

    func move(to destination: URL) {
        guard !audio.preparingUpdate,!stopped else { return }
        if operation == .downloading {
            pendingMove = destination
            if let currentModelID,!queuedModelIDs.contains(currentModelID) { queuedModelIDs.insert(currentModelID,at:0) }
            cancel(preserveQueue:true); return
        }
        guard canManage else { return }
        start(.moving) { owner in
            try await owner.change(command:"move-models",options:["--destination",destination.path])
            owner.message = "模型存储位置已更新，目录中的其他文件已保留。"
            try await owner.loadStatus()
        }
    }

    func delete(_ model: ManagedModel) {
        guard canManage,!model.active else { return }
        start(.deleting,modelID:model.id) { owner in
            _ = try await owner.preparation.manage("delete-model",options:["--model",model.id])
            owner.modelErrors[model.id] = nil
            owner.message = "已删除未使用模型，其他文件与共用下载暂存已保留。"
            try await owner.loadStatus()
        }
    }

    private func change(command: String, options: [String]) async throws {
        guard !audio.preparingUpdate else { throw CyberAudioError.message("应用正在准备更新，请稍后再修改模型。") }
        audio.managingModels = true
        defer { audio.managingModels = false }
        let wasRunning = backend.running
        let lease = UUID().uuidString
        var engineStopped = false
        var heartbeat: Task<Void,Never>?
        defer { heartbeat?.cancel() }
        do {
            message = "等待录音、播放与当前任务结束…"
            while audio.working || audio.playing || dictation.blocksUpdate {
                try Task.checkCancellation(); try await Task.sleep(for:.milliseconds(200))
            }
            message = "暂停接收新请求，等待引擎空闲…"
            while !(try await backend.prepareForUpdate(lease:lease)) {
                try Task.checkCancellation(); try await Task.sleep(for:.seconds(1))
            }
            try Task.checkCancellation()
            await backend.stop(); engineStopped = true
            message = command == "move-models" ? "正在迁移并验证模型…" : "正在应用模型选择…"
            _ = try await preparation.manage(command,options:options+["--transaction","true"])
            try Task.checkCancellation()
            try backend.reloadConfiguration()
            if wasRunning {
                backend.startPrepared(maintenanceLease:lease)
                heartbeat = Task { @MainActor [backend] in
                    while !Task.isCancelled {
                        _ = try? await backend.prepareForUpdate(lease:lease)
                        try? await Task.sleep(for:.seconds(5))
                    }
                }
                try await waitForReady()
            }
            cancellable = false; message = "正在完成配置…"
            _ = try await preparation.manage("commit-model-change")
            heartbeat?.cancel()
            if wasRunning { await backend.cancelUpdatePreparation(lease:lease) }
        } catch {
            heartbeat?.cancel()
            guard !stopped else { throw error }
            let originalError = error
            try await Task { @MainActor in
                if engineStopped {
                    await backend.stop()
                    _ = try await preparation.manage("rollback-model-change")
                    try backend.reloadConfiguration()
                    if wasRunning { backend.startPrepared(); try await waitForReady() }
                } else { await backend.cancelUpdatePreparation(lease:lease) }
            }.value
            throw originalError
        }
    }

    private func waitForReady() async throws {
        let deadline = Date().addingTimeInterval(240)
        while !backend.ready {
            try Task.checkCancellation()
            guard backend.running,backend.status != "failed",Date() < deadline else {
                throw CyberAudioError.message("新模型无法就绪，正在恢复原配置。")
            }
            message = "正在加载模型…"
            try await Task.sleep(for:.milliseconds(250))
        }
    }

    private func start(_ operation: Operation, modelID: String? = nil,
                       work: @escaping (ModelManagerController) async throws -> Void) {
        guard task == nil,!stopped else { return }
        self.operation = operation; currentModelID = modelID; error = nil
        if operation == .checking { message = "正在检查模型…" }
        cancellable = operation != .checking && operation != .deleting
        task = Task { [weak self] in
            guard let self else { return }
            do { try await work(self) }
            catch {
                guard !stopped else { return }
                if Task.isCancelled || error is CancellationError { message = "已取消，已保存的下载进度会保留。" }
                else {
                    self.error = error.localizedDescription
                    if let modelID { modelErrors[modelID] = error.localizedDescription }
                }
                try? await Task { @MainActor in try await self.loadStatus() }.value
            }
            guard !stopped else { return }
            if operation == .checking { message = "" }
            self.operation = nil; currentModelID = nil; cancellable = false; task = nil
            if let destination = pendingMove {
                pendingMove = nil; move(to:destination)
            } else if !queuedModelIDs.isEmpty { nextDownload() }
            else if operation != .checking,!preparation.ready,preparation.snapshot?.ffmpeg_ready == true,let snapshot,
                    snapshot.models.filter(\.active).allSatisfy({ $0.state == "ready" }) {
                preparation.check(startWhenReady:false)
            }
        }
    }

    func cancel(preserveQueue: Bool = false) {
        guard cancellable else { return }
        if !preserveQueue { queuedModelIDs = [] }
        task?.cancel(); preparation.cancel(); cancellable = false
    }
    func shutdown() {
        stopped = true; queuedModelIDs = []; pendingMove = nil; task?.cancel()
    }
}
