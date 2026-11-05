import AppKit
import Combine
import Foundation

struct ServiceConfiguration: Codable {
    var asr_dir: String
    var tts_dir: String
    var ffmpeg: String
    var port: Int
    var https_url: String
}

struct Readiness: Decodable {
    let status: String
    let busy: Bool
    let instance: String?
    let startup: [String: MetricValue]?
}

private struct UpdatePreparation: Decodable {
    let instance: String
    let busy: Bool
    let updating: Bool
}

enum MetricValue: Decodable {
    case number(Double), text(String)
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Double.self) { self = .number(value) }
        else { self = .text(try container.decode(String.self)) }
    }
    var display: String {
        switch self {
        case .number(let value): return String(format: "%.0f", value)
        case .text(let value): return value
        }
    }
}

struct APIErrorEnvelope: Decodable {
    struct Detail: Decodable { let code: String; let message: String }
    let error: Detail
}

enum CyberAudioError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}

@MainActor
final class BackendController: ObservableObject {
    @Published private(set) var status = "stopped"
    @Published private(set) var busy = false
    @Published private(set) var detail = "尚未启动"
    @Published private(set) var startup: [String: MetricValue] = [:]
    @Published private(set) var configuration: ServiceConfiguration?
    private var child: Process?
    private var input: Pipe?
    private var poll: Task<Void, Never>?
    private var instance = ""
    private var token = ""
    private var stopping = false
    let root: URL
    let session: URLSession

    init() {
        let arguments = ProcessInfo.processInfo.arguments
        let index = arguments.firstIndex(of: "--data-dir")
        let override = index.flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil }
            ?? ProcessInfo.processInfo.environment["CYBERAUDIO_HOME"]
        let updateRoot = UserDefaults.standard.string(forKey: "PendingUpdateDataDirectory")
        UserDefaults.standard.removeObject(forKey: "PendingUpdateDataDirectory")
        root = (override ?? updateRoot).map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/CyberAudio", isDirectory: true)
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 150
        config.timeoutIntervalForResource = 180
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    var ready: Bool { status == "ready" }
    var running: Bool { child?.isRunning == true }
    var localURL: String { "http://127.0.0.1:\(configuration?.port ?? 8765)" }
    var httpsURL: String { configuration?.https_url ?? "未配置" }
    var statusLabel: String {
        if busy { return "正在处理" }
        return ["ready": "就绪", "loading": "加载模型", "stopped": "已停止", "stopping": "正在停止", "failed": "启动失败"][status] ?? "连接中断"
    }

    func start() {
        guard child == nil, !stopping else { return }
        status = "loading"
        detail = "正在启动本地推理引擎…"
        do {
            let configURL = root.appendingPathComponent("config/service.json")
            configuration = try JSONDecoder().decode(ServiceConfiguration.self, from: Data(contentsOf: configURL))
            token = try String(contentsOf: root.appendingPathComponent("config/api-token"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
            guard token.count >= 32 else { throw CyberAudioError.message("API Token 配置无效。") }
            let helper = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/CyberAudioService")
            guard let resources = Bundle.main.resourceURL,
                  FileManager.default.isExecutableFile(atPath: helper.path),
                  FileManager.default.fileExists(atPath: resources.appendingPathComponent("mlx.metallib").path),
                  FileManager.default.fileExists(atPath: resources.appendingPathComponent("offline.sb").path) else {
                throw CyberAudioError.message("App 缺少原生推理资源，请重新安装完整 App。")
            }
            instance = UUID().uuidString
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/sandbox-exec")
            process.arguments = ["-f", resources.appendingPathComponent("offline.sb").path, helper.path,
                                 "--root", root.path, "--parent-pid", String(getpid()), "--instance", instance,
                                 "--metallib", resources.appendingPathComponent("mlx.metallib").path]
            process.environment = ProcessInfo.processInfo.environment.merging([
                "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            ]) { _, new in new }
            let pipe = Pipe()
            process.standardInput = pipe
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            process.currentDirectoryURL = root
            process.terminationHandler = { [weak self] process in
                Task { @MainActor in
                    guard let self, self.child === process else { return }
                    self.child = nil
                    self.input = nil
                    self.busy = false
                    if !self.stopping {
                        self.poll?.cancel()
                        self.status = "failed"
                        self.detail = "推理引擎已退出（\(process.terminationStatus)）。请查看日志后重新启动。"
                    }
                }
            }
            child = process
            input = pipe
            try process.run()
            // Only the child's read endpoint and this writer remain open.
            try? pipe.fileHandleForReading.close()
            poll = Task { [weak self] in
                guard let self else { return }
                let started = Date()
                while !Task.isCancelled, self.child != nil {
                    do {
                        let (data, _) = try await self.session.data(from: URL(string: self.localURL + "/ready")!)
                        let value = try JSONDecoder().decode(Readiness.self, from: data)
                        if value.instance != self.instance {
                            self.detail = "端口已被其他服务占用，请先停用旧 ASR 服务。"
                            self.status = "failed"
                        } else {
                            self.status = value.status
                            self.busy = value.busy
                            self.startup = value.startup ?? [:]
                            self.detail = value.status == "ready" ? "STT 与 TTS 已加载 · Apple GPU" : (value.status == "failed" ? "模型加载失败，请打开日志检查。" : "首次预热可能需要数十秒…")
                        }
                    } catch {
                        if Date().timeIntervalSince(started) > 180, self.status == "loading" {
                            self.status = "failed"
                            self.detail = "启动超过 180 秒，请检查模型文件和日志。"
                        }
                    }
                    try? await Task.sleep(for: .seconds(1))
                }
            }
        } catch {
            child = nil
            input = nil
            status = "failed"
            detail = "无法启动：\(error.localizedDescription)"
        }
    }

    func stop() async {
        guard !stopping else {
            while stopping { try? await Task.sleep(for: .milliseconds(100)) }
            return
        }
        stopping = true
        status = "stopping"
        poll?.cancel()
        poll = nil
        let process = child
        try? input?.fileHandleForWriting.close()
        input = nil
        if let process, process.isRunning {
            process.terminate()
            for _ in 0..<90 {
                if !process.isRunning { break }
                try? await Task.sleep(for: .milliseconds(100))
            }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
        child = nil
        status = "stopped"
        detail = "API 已停止，模型已卸载"
        busy = false
        stopping = false
    }

    func restart() async { await stop(); start() }

    // NSApplication can enter a nested termination run loop. Finish owned-process cleanup
    // synchronously here, so quitting never depends on another main-actor task being scheduled.
    func stopForTermination() {
        poll?.cancel()
        poll = nil
        try? input?.fileHandleForWriting.close()
        input = nil
        guard let process = child, process.isRunning else { child = nil; return }
        process.terminate()
        let deadline = Date().addingTimeInterval(9)
        while process.isRunning, Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        child = nil
    }
    func copyToken() { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(token, forType: .string) }

    func preserveRootForUpdateRelaunch() {
        // Sparkle relaunches the bundle without the original command-line arguments.
        UserDefaults.standard.set(root.path, forKey: "PendingUpdateDataDirectory")
    }

    func prepareForUpdate(lease: String) async throws -> Bool {
        guard running else { return true }
        let value = try await updateControl("prepare", lease: lease)
        guard value.updating else { throw CyberAudioError.message("引擎未进入更新准备状态。") }
        return !value.busy
    }

    func cancelUpdatePreparation(lease: String) async {
        guard running else { return }
        _ = try? await updateControl("cancel", lease: lease)
    }

    private func updateControl(_ action: String, lease: String) async throws -> UpdatePreparation {
        var request = URLRequest(url: URL(string: localURL + "/internal/update/" + action)!)
        request.httpMethod = "POST"
        request.timeoutInterval = 3
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(instance, forHTTPHeaderField: "X-CyberAudio-Instance")
        request.setValue(lease, forHTTPHeaderField: "X-CyberAudio-Update")
        let (data, response) = try await session.data(for: request)
        try check(response, data: data)
        let value = try JSONDecoder().decode(UpdatePreparation.self, from: data)
        guard value.instance == instance else { throw CyberAudioError.message("更新连接不属于当前引擎。") }
        return value
    }

    func request(path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil) throws -> URLRequest {
        guard ready else { throw CyberAudioError.message("模型尚未就绪。") }
        guard let url = URL(string: localURL + path) else { throw CyberAudioError.message("API 地址无效。") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        request.httpBody = body
        return request
    }

    func check(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let envelope = try? JSONDecoder().decode(APIErrorEnvelope.self, from: data)
            throw CyberAudioError.message(envelope?.error.message ?? "API 请求失败，请检查服务状态。")
        }
    }
}
