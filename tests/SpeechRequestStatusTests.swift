import Darwin
import Foundation

// One real loopback response per case. The peer can drip an unfinished body
// to distinguish a complete-response deadline from an inactivity timeout.
private final class StatusEndpoint: @unchecked Sendable {
    let port: UInt16
    private let listener: Int32
    private let lock = NSLock()
    private var peer: Int32 = -1
    private var stopped = false
    private let stopSignal = DispatchSemaphore(value: 0)

    init(body: String, drip: Bool = false) throws {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw URLError(.cannotCreateFile) }
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
        guard bound == 0, named == 0, listen(fd, 1) == 0 else { close(fd); throw URLError(.cannotConnectToHost) }
        listener = fd
        port = UInt16(bigEndian: address.sin_port)
        DispatchQueue.global().async { [self] in
            let client = accept(fd, nil, nil)
            guard client >= 0 else { return }
            lock.lock()
            if stopped { lock.unlock(); close(client); return }
            peer = client
            lock.unlock()
            defer { lock.lock(); peer = -1; close(client); lock.unlock() }
            var yes: Int32 = 1
            _ = setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
            var input = [UInt8](repeating: 0, count: 4096)
            var request = Data()
            while request.range(of: Data("\r\n\r\n".utf8)) == nil, request.count < 16384 {
                let count = recv(client, &input, input.count, 0)
                guard count > 0 else { return }
                request.append(contentsOf: input.prefix(count))
            }
            func send(_ data: Data) -> Bool {
                data.withUnsafeBytes { buffer in
                    var offset = 0
                    while offset < buffer.count {
                        let count = Darwin.send(client, buffer.baseAddress!.advanced(by: offset), buffer.count - offset, 0)
                        guard count > 0 else { return false }
                        offset += count
                    }
                    return true
                }
            }
            let data = Data(body.utf8)
            guard send(Data("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(drip ? 100 : data.count)\r\nConnection: close\r\n\r\n".utf8)) else { return }
            if drip {
                for _ in 0..<40 {
                    guard send(Data([32])), stopSignal.wait(timeout: .now() + 0.2) == .timedOut else { return }
                }
            } else { _ = send(data) }
        }
    }

    func stop() {
        lock.lock(); defer { lock.unlock() }
        guard !stopped else { return }
        stopped = true
        shutdown(listener, SHUT_RDWR); close(listener)
        if peer >= 0 { shutdown(peer, SHUT_RDWR) }
        stopSignal.signal()
    }

    var request: URLRequest { URLRequest(url: URL(string: "http://127.0.0.1:\(port)/v1/requests/0123456789abcdef")!) }
}

@main
struct SpeechRequestStatusTests {
    struct Failure: Error { let message: String }
    static func require(_ value: Bool, _ message: String) throws { if !value { throw Failure(message: message) } }

    static func main() async throws {
        let id = "0123456789abcdef"
        let bodies = [
            #"{"request_id":"0123456789abcdef","kind":"tts","status":"failed","code":"generation_limit","message":"Specific safe failure"}"#,
            #"{"request_id":"0123456789abcdef","kind":"tts","status":"completed"}"#,
            #"{"request_id":"0123456789abcdef","kind":"tts","status":"failed","code":"generation_limit"}"#,
        ]
        for (index, body) in bodies.enumerated() {
            let endpoint = try StatusEndpoint(body: body)
            defer { endpoint.stop() }
            let state = try await SpeechRequestStatus.fetch(endpoint.request, expectedID: id)
            switch index {
            case 0: try require(state.failure?.localizedDescription == "Specific safe failure", "Safe terminal reason was lost")
            case 1: try require(state.status == .completed && state.failure == nil, "A completed record must not replace a transport failure")
            default: try require(state.code == "generation_limit" && state.failure != nil, "Old code-only Helper failures must remain usable")
            }
        }
        for body in [
            #"{"request_id":"fedcba9876543210","kind":"tts","status":"failed","message":"Wrong request"}"#,
            #"{"request_id":"0123456789abcdef","kind":"stt","status":"failed","message":"Wrong operation"}"#,
            #"{"request_id":"0123456789abcdef","kind":"tts","status":"future-state"}"#,
        ] {
            let endpoint = try StatusEndpoint(body: body)
            defer { endpoint.stop() }
            var rejected = false
            do { _ = try await SpeechRequestStatus.fetch(endpoint.request, expectedID: id) }
            catch { rejected = true }
            try require(rejected, "A mismatched or unknown record cannot authorize completion or replace an error")
        }
        let invalid = try StatusEndpoint(body: bodies[0])
        defer { invalid.stop() }
        var rejected = false
        do { _ = try await SpeechRequestStatus.fetch(invalid.request, expectedID: "../other-request") }
        catch { rejected = true }
        try require(rejected, "Invalid request IDs must be rejected")

        let slow = try StatusEndpoint(body: "", drip: true)
        defer { slow.stop() }
        let began = ProcessInfo.processInfo.systemUptime
        var timeout: Error?
        do { _ = try await SpeechRequestStatus.fetch(slow.request, expectedID: id) }
        catch { timeout = error }
        try require((timeout as? URLError)?.code == .timedOut, "A progressing but unfinished body must hit the complete-response deadline")
        try require(ProcessInfo.processInfo.systemUptime - began < 5, "Status lookup retained its caller beyond the bounded deadline")

        let cancelled = try StatusEndpoint(body: "", drip: true)
        defer { cancelled.stop() }
        let task = Task { try await SpeechRequestStatus.fetch(cancelled.request, expectedID: id) }
        try await Task.sleep(for: .milliseconds(100))
        let cancellationStart = ProcessInfo.processInfo.systemUptime
        task.cancel()
        var cancellation: Error?
        do { _ = try await task.value } catch { cancellation = error }
        try require(cancellation is CancellationError || (cancellation as? URLError)?.code == .cancelled, "Cancellation must not become a usable terminal record")
        try require(ProcessInfo.processInfo.systemUptime - cancellationStart < 1.5, "Cancelling a status query must release its caller promptly")
        print("SpeechRequestStatus: 9 real HTTP/identity/compatibility/deadline/cancellation cases passed")
    }
}
