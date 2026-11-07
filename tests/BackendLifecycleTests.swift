import AppKit
import Combine
import Darwin
import Foundation

// Hold only /ready at the URL loading boundary. The production controller still
// owns and stops the real native test Helper, including its termination callback.
private final class ReadinessProtocol: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    private static var pending: [String: ReadinessProtocol] = [:]
    override class func canInit(with request: URLRequest) -> Bool { request.url?.path == "/ready" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let id = request.value(forHTTPHeaderField: "X-CyberAudio-Test-Run") else { return }
        Self.lock.lock(); Self.pending[id] = self; Self.lock.unlock()
    }
    override func stopLoading() {}

    static func take(_ id: String) -> ReadinessProtocol? {
        lock.lock(); defer { lock.unlock() }
        return pending.removeValue(forKey: id)
    }

    func finish(instance: String, busy: Bool = false) throws {
        let data = try JSONSerialization.data(withJSONObject: [
            "status":"ready", "busy":busy, "instance":instance, "application":"CyberAudio",
            "model":"qwen3-asr-1.7b", "models":["stt":"ready", "tts":"ready"],
            "startup":["load_ms":1, "warmup_ms":1, "device":"synthetic-fixture"]
        ])
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type":"application/json",
                "Content-Length":String(data.count)])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
}

@main @MainActor struct BackendLifecycleTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func until(_ condition: () -> Bool, _ message: String) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            try require(Date() < deadline, message)
            try await Task.sleep(for: .milliseconds(5))
        }
    }
    private static func pendingResponse(_ id: String) async throws -> ReadinessProtocol {
        var response: ReadinessProtocol?
        try await until({ response = ReadinessProtocol.take(id); return response != nil }, "Readiness request did not start")
        return response!
    }
    static func instance(of backend: BackendController) throws -> String {
        guard let value = Mirror(reflecting: backend).children.first(where: { $0.label == "instance" })?.value as? String else {
            throw Failure(message: "Missing engine instance")
        }
        return value
    }
    static func availablePort() throws -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try require(fd >= 0, "Cannot allocate a test port")
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        var size = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &size) }
        }
        try require(bound == 0 && named == 0, "Cannot select a test port")
        return Int(UInt16(bigEndian: address.sin_port))
    }

    // Complete the network operation while MainActor is occupied. Waiting for
    // URLSession's task to finish fixes the race ordering without an arbitrary delay.
    private static func queueCompletedResponse(_ response: ReadinessProtocol, session: URLSession, instance: String) throws {
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            do { try response.finish(instance: instance, busy: true) } catch { return }
            let completed = DispatchSemaphore(value: 0)
            for _ in 0..<100 {
                let inspected = DispatchSemaphore(value: 0)
                session.getAllTasks { tasks in
                    if tasks.allSatisfy({ $0.state == .completed }) { completed.signal() }
                    inspected.signal()
                }
                guard inspected.wait(timeout: .now() + 1) == .success else { return }
                if completed.wait(timeout: .now()) == .success { done.signal(); return }
                Thread.sleep(forTimeInterval: 0.01)
            }
        }
        try require(done.wait(timeout: .now() + 5) == .success, "URLSession did not complete the controlled response")
    }

    static func run(_ mode: String) async throws {
        let id = UUID().uuidString
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ReadinessProtocol.self]
        configuration.httpAdditionalHeaders = ["X-CyberAudio-Test-Run":id]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let backend = BackendController(session: session)
        defer { backend.stopForTermination(); _ = ReadinessProtocol.take(id) }
        try FileManager.default.createDirectory(at: backend.root.appendingPathComponent("config"), withIntermediateDirectories: true)
        try JSONEncoder().encode(ServiceConfiguration(asr_dir: backend.root.path, tts_dir: backend.root.path,
            ffmpeg: "/opt/homebrew/bin/ffmpeg", port: availablePort(), https_url: ""))
            .write(to: backend.root.appendingPathComponent("config/service.json"))
        try String(repeating: "f", count: 64).write(to: backend.root.appendingPathComponent("config/api-token"), atomically: true, encoding: .utf8)
        backend.startPrepared()
        try require(backend.running, "Test Helper failed to start")
        let response = try await pendingResponse(id), originalInstance = try instance(of: backend)

        if mode == "current" {
            try response.finish(instance: originalInstance, busy: true)
            try await until({ backend.ready }, "The current response was not accepted")
            try require(backend.running && backend.busy && backend.startup["device"]?.display == "synthetic-fixture",
                "Current readiness must publish busy state and startup metrics")
        } else if mode == "wrong-instance" {
            try response.finish(instance: "another-engine")
            try await until({ backend.status == "failed" }, "Current wrong-instance response was not rejected")
            try require(backend.running && backend.detail.contains("端口"), "Current port conflicts must remain actionable")
        } else {
            try queueCompletedResponse(response, session: session, instance: originalInstance)
            var states: [String] = []
            let observation = backend.$status.dropFirst().sink { states.append($0) }
            defer { observation.cancel() }
            if mode != "stop" { backend.stopForTermination() }
            await backend.stop()
            if mode == "restart" {
                backend.startPrepared()
                let replacementInstance = try instance(of: backend)
                try require(replacementInstance != originalInstance, "Restart must create another engine instance")
            }
            // Give queued actor work a turn after the completed response and stop.
            try await Task.sleep(for: .milliseconds(50))
            if mode == "restart" {
                try require(backend.status == "loading" && backend.running && !backend.busy,
                    "An old response must not fail the replacement engine or publish its busy state: \(states)")
                let current = try await pendingResponse(id)
                try current.finish(instance: instance(of: backend))
                try await until({ backend.ready }, "The replacement engine's response was not accepted")
            } else {
                try require(backend.status == "stopped" && !backend.running && !backend.busy && !states.contains("ready"),
                    "A stale response must not revive a stopped or stopping engine: \(states)")
            }
        }
        await backend.stop()
        try require(backend.status == "stopped" && !backend.running && !backend.busy, "Engine did not stop cleanly")
    }

    static func main() async {
        var failures = 0
        for mode in ["stop", "termination", "restart", "current", "wrong-instance"] {
            do { try await run(mode); print("BackendLifecycle \(mode): passed") }
            catch { failures += 1; fputs("BackendLifecycle \(mode): FAIL \(error)\n", stderr) }
        }
        if failures > 0 { exit(1) }
        print("BackendLifecycle: 5 readiness ownership and process lifecycle cases passed")
    }
}
