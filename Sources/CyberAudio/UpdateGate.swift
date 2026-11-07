import Combine
import Foundation

/// Pauses admission only after local audio finishes, then drains external inference.
@MainActor
final class UpdateGate: ObservableObject {
    @Published private(set) var waiting = false
    @Published private(set) var terminationAllowed = false
    @Published private(set) var message = ""
    @Published private(set) var failure: String?
    private let audioBusy: () -> Bool
    private let prepare: (String) async throws -> Bool
    private let resume: (String) async -> Void
    private var task: Task<Void, Never>?
    private var activeLease: String?

    init(audioBusy: @escaping () -> Bool, prepare: @escaping (String) async throws -> Bool,
         resume: @escaping (String) async -> Void) {
        self.audioBusy = audioBusy
        self.prepare = prepare
        self.resume = resume
    }

    func begin(install: @escaping () -> Void, cancelled: @escaping () -> Void = {}) {
        guard !waiting, !terminationAllowed else { return }
        waiting = true
        failure = nil
        let lease = UUID().uuidString
        activeLease = lease
        task = Task {
            do {
                while true {
                    try Task.checkCancellation()
                    if audioBusy() {
                        message = "等待录音、播放与当前任务结束…"
                        try await Task.sleep(for: .milliseconds(200))
                        continue
                    }
                    message = "暂停接收新请求，等待引擎空闲…"
                    let idle = try await prepare(lease)
                    try Task.checkCancellation()
                    // Preparation can start while the asynchronous handshake is pending.
                    if idle && !audioBusy() { break }
                    try await Task.sleep(for: .seconds(1))
                }
                terminationAllowed = true
                waiting = false
                message = "正在安装更新…"
                task = nil
                install()
            } catch {
                let wasCancelled = Task.isCancelled || error is CancellationError
                // Cleanup must run outside the cancelled task so URLSession can send it.
                await Task { await self.resume(lease) }.value
                activeLease = nil
                failure = wasCancelled ? nil : "暂时无法安全重启：\(error.localizedDescription)"
                message = wasCancelled ? "已取消等待，可继续使用" : "更新尚未安装，请重试"
                waiting = false
                task = nil
                cancelled()
            }
        }
    }

    func cancel() {
        guard waiting else { return }
        message = "正在恢复服务…"
        task?.cancel()
    }

    func reset() {
        if waiting { cancel(); return }
        terminationAllowed = false
        if let lease = activeLease {
            activeLease = nil
            Task { await resume(lease) }
        }
    }
}
