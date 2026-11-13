import Combine
import Foundation
import Darwin

struct PreparationSnapshot: Decodable {
    let total_bytes: Int64
    let verified_bytes: Int64
    let resumable_bytes: Int64
    let missing_files: Int
    let invalid_files: [String]
    let invalid_partials: [String]
    let models_ready: Bool
    let ffmpeg_ready: Bool
    let ffmpeg_path: String
    let available_bytes: Int64?
    let required_bytes: Int64
    let can_download: Bool
}

enum PreparationDownloadSource: String {
    case huggingFace = "huggingface"
    case modelScope = "modelscope"
}

private struct PreparationEvent: Decodable {
    let event: String
    let path: String?
    let completed_bytes: Int64?
    let total_bytes: Int64?
    let phase: String?
    let message: String?
}

struct PreparationResult {
    var snapshot: PreparationSnapshot?
    var terminal = false
    var payload: Data?
}

private enum PreparationFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

/// One owned helper at a time. Cancellation retains ownership until its pipe closes.
final class PreparationCommand: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false
    private var timedOut = false

    func run(helper: URL, root: URL, command: String, options: [String] = [], timeout: TimeInterval,
             progress: @escaping @Sendable (Int64, Int64, String, String) -> Void) async throws -> PreparationResult {
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .utility).async {
                    do { continuation.resume(returning: try self.perform(helper: helper, root: root, command: command, options: options, timeout: timeout, progress: progress)) }
                    catch { continuation.resume(throwing: error) }
                }
            }
        }, onCancel: { self.cancel() })
    }

    private func perform(helper: URL, root: URL, command: String, options: [String], timeout: TimeInterval,
                         progress: @escaping @Sendable (Int64, Int64, String, String) -> Void) throws -> PreparationResult {
        let child = Process(), pipe = Pipe()
        child.executableURL = helper
        child.arguments = [command, "--root", root.path, "--json", "true", "--parent-pid", String(getpid())] + options
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = pipe
        child.standardError = FileHandle.nullDevice
        // The helper never receives the user's shell or proxy environment.
        child.environment = ["PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                             "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                             "TMPDIR": FileManager.default.temporaryDirectory.path]
        defer {
            if child.isRunning { child.terminate(); child.waitUntilExit() }
            try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close()
            lock.lock(); if process === child { process = nil }; lock.unlock()
        }
        lock.lock()
        if cancelled { lock.unlock(); throw CancellationError() }
        process = child
        do { try child.run(); lock.unlock() }
        catch { process = nil; lock.unlock(); throw PreparationFailure.message("准备工具无法启动，请重新安装完整 App。") }
        let watchdog = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        watchdog.schedule(deadline: .now() + timeout)
        watchdog.setEventHandler { [weak self] in
            guard let self else { return }
            self.lock.lock(); self.timedOut = true; self.lock.unlock()
            self.cancel()
        }
        watchdog.resume()
        defer { watchdog.cancel() }
        try? pipe.fileHandleForWriting.close()
        var pending = Data(), result = PreparationResult(), failure: String?
        do {
            while true {
                let chunk = pipe.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 10) {
                    guard newline < 65_536 else { throw PreparationFailure.message("准备工具返回了无效数据，请重新安装 App。") }
                    let line = Data(pending[..<newline]); pending.removeSubrange(...newline)
                    let event = try JSONDecoder().decode(PreparationEvent.self, from: line)
                    switch event.event {
                    case "models", "catalog", "managed": result.payload = line; result.terminal = true
                    case "status":
                        result.snapshot = try JSONDecoder().decode(PreparationSnapshot.self, from: line); result.terminal = true
                    case "prepared", "verified": result.terminal = true
                    case "error": failure = String((event.message ?? "资源准备失败，请重试。").prefix(500))
                    case "progress":
                        if let completed = event.completed_bytes, let total = event.total_bytes, total > 0, completed >= 0, completed <= total {
                            progress(completed, total, String((event.path ?? "").prefix(160)), event.phase ?? "downloading")
                        }
                    case "phase": progress(-1, 0, "", event.phase ?? "verifying")
                    default: throw PreparationFailure.message("准备工具版本不兼容，请重新安装 App。")
                    }
                }
                guard pending.count < 65_536 else { throw PreparationFailure.message("准备工具返回了无效数据，请重新安装 App。") }
            }
        } catch {
            cancel()
            throw PreparationFailure.message("无法读取准备结果，请重试或重新安装 App。")
        }
        child.waitUntilExit()
        lock.lock(); let wasCancelled = cancelled, expired = timedOut; lock.unlock()
        if expired { throw PreparationFailure.message("资源准备超时，已保留下载进度，请重试。") }
        if wasCancelled { throw CancellationError() }
        if let failure { throw PreparationFailure.message(failure) }
        guard child.terminationStatus == 0, result.terminal, pending.isEmpty else {
            throw PreparationFailure.message("资源准备未完成，请检查网络与目录权限后重试。")
        }
        return result
    }

    func cancel() {
        lock.lock(); cancelled = true; let child = process; lock.unlock()
        guard let child, child.isRunning else { return }
        child.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
            if child.isRunning { kill(child.processIdentifier, SIGKILL) }
        }
    }

    func shutdown() {
        cancel()
        lock.lock(); let child = process; lock.unlock()
        let deadline = Date().addingTimeInterval(2)
        while child?.isRunning == true && Date() < deadline { Thread.sleep(forTimeInterval: 0.01) }
        if let child, child.isRunning { kill(child.processIdentifier, SIGKILL) }
    }
}

@MainActor
final class PreparationController: ObservableObject {
    enum Phase { case checking, needsSetup, downloading, cancelling, cancelled, failed, ready }
    @Published private(set) var phase: Phase = .checking
    @Published private(set) var snapshot: PreparationSnapshot?
    @Published private(set) var message = "正在检查本地资源…"
    @Published private(set) var completedBytes: Int64 = 0
    @Published private(set) var totalBytes: Int64 = 0
    @Published private(set) var currentFile = ""
    @Published private(set) var verifying = false
    @Published private(set) var busy = false
    @Published private(set) var managing = false
    @Published var downloadSource: PreparationDownloadSource = .huggingFace
    @Published var proxy = ""
    private let root: URL
    private let helper: URL
    private let onReady: () -> Void
    private let commandTimeout: TimeInterval
    private var task: Task<Void, Never>?
    private var command: PreparationCommand?
    private var attempt = UUID()
    private var stopped = false
    var ready: Bool { phase == .ready }

    init(root: URL, helper: URL? = nil, commandTimeout: TimeInterval = 180, onReady: @escaping () -> Void) {
        self.root = root
        self.helper = helper ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/CyberAudioAssets")
        self.onReady = onReady
        self.commandTimeout = commandTimeout
    }

    func check(startWhenReady: Bool = true) { begin(.checking,startWhenReady:startWhenReady) { controller in
        _ = try await controller.run("prepare")
        try await controller.inspect(verify: true)
    } }

    func download(restart: Bool = false) {
        guard snapshot?.can_download == true else { return }
        let source = downloadSource
        let proxy = proxy.trimmingCharacters(in: .whitespacesAndNewlines)
        begin(.downloading) { controller in
            var options = ["--restart", restart ? "true" : "false", "--source", source.rawValue]
            if !proxy.isEmpty { options += ["--proxy", proxy] }
            _ = try await controller.run("download", options: options)
            try await controller.inspect(verify: false)
        }
    }

    func selectFFmpeg(_ url: URL) { begin(.checking) { controller in
        let result = try await controller.run("set-ffmpeg", options: ["--ffmpeg", url.path])
        controller.snapshot = result.snapshot
        try await controller.inspect(verify: true)
    } }

    /// Settings and first-run preparation share one owned helper. Managing an
    /// inactive model must not replace the ready workspace with first-run UI.
    func manage(_ name: String, options: [String] = []) async throws -> Data? {
        guard !busy, !stopped else { throw PreparationFailure.message("已有模型任务正在进行，请等待任务结束。") }
        attempt = UUID(); busy = true; managing = true; verifying = false; currentFile = ""
        completedBytes = 0; totalBytes = 0
        defer { command = nil; busy = false; managing = false; verifying = false }
        return try await run(name, options: options).payload
    }

    private func inspect(verify: Bool) async throws {
        let result = try await run("status")
        guard let status = result.snapshot else { throw PreparationFailure.message("无法读取资源状态，请重新检查。") }
        try Task.checkCancellation()
        snapshot = status
        completedBytes = status.verified_bytes + status.resumable_bytes; totalBytes = status.total_bytes
        if status.models_ready && status.ffmpeg_ready {
            if verify { verifying = true; _ = try await run("verify") }
            try Task.checkCancellation()
            phase = .ready; message = "本地资源已就绪"
        } else {
            phase = .needsSetup
            if !status.invalid_files.isEmpty { message = "已有模型文件校验失败，文件已保留。请检查模型目录后重新检查。" }
            else if let available = status.available_bytes, available < status.required_bytes { message = "磁盘空间不足，请释放空间后重新检查。" }
            else if !status.invalid_partials.isEmpty { message = "暂存下载校验失败，请选择重新下载。" }
            else { message = "准备完成后，语音识别与合成都在本机运行。" }
        }
    }

    private func begin(_ phase: Phase, startWhenReady: Bool = true, operation: @escaping (PreparationController) async throws -> Void) {
        guard !busy, !stopped else { return }
        attempt = UUID(); let id = attempt
        self.phase = phase; busy = true; verifying = false; currentFile = ""
        if phase == .checking { message = "正在检查本地资源…" }
        else { completedBytes = 0; message = "正在下载，已完成的文件与进度会保留。" }
        task = Task { [weak self] in
            guard let self else { return }
            do {
                try await operation(self)
                try Task.checkCancellation()
                guard attempt == id, !stopped else { return }
                command = nil; task = nil; busy = false; verifying = false
                if ready && startWhenReady { onReady() }
            } catch {
                guard attempt == id, !stopped else { return }
                let cancelled = Task.isCancelled || error is CancellationError
                self.phase = cancelled ? .cancelled : .failed
                message = cancelled ? "已取消，已完成的下载进度会保留。" : error.localizedDescription
                command = nil; task = nil; busy = false; verifying = false
            }
        }
    }

    private func run(_ name: String, options: [String] = []) async throws -> PreparationResult {
        try Task.checkCancellation()
        let child = PreparationCommand(), id = attempt
        command = child
        let result = try await child.run(helper: helper, root: root, command: name, options: options,
                                         timeout: ["download", "move-models"].contains(name) ? 3600 : commandTimeout) { [weak self] completed, total, path, phase in
            Task { @MainActor in
                guard let self, self.attempt == id, self.busy, self.phase != .cancelling, !self.stopped else { return }
                self.verifying = phase == "verifying"
                if completed >= 0 { self.completedBytes = completed; self.totalBytes = total; self.currentFile = path }
            }
        }
        try Task.checkCancellation()
        return result
    }

    func cancel() {
        guard busy else { return }
        if !managing { phase = .cancelling }
        message = "正在取消并保留下载进度…"
        task?.cancel(); command?.cancel()
    }

    func shutdown() {
        stopped = true; attempt = UUID()
        task?.cancel(); command?.shutdown()
        task = nil; command = nil; busy = false
    }
}
