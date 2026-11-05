import Foundation

// Standalone tests also run on Macs with Command Line Tools and no XCTest bundle.
@main
@MainActor
struct UpdateGateTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func until(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(3)
        while !condition() {
            try require(Date() < deadline, "Timed out waiting for update gate")
            try await Task.sleep(for: .milliseconds(10))
        }
    }
    static func main() async throws {
        var busy = true, prepared = false, installed = false
        let local = UpdateGate(audioBusy: { busy }, prepare: { _ in prepared = true; return true }, resume: { _ in })
        local.begin { installed = true }
        try await Task.sleep(for: .milliseconds(50))
        try require(!prepared && !installed, "Local audio must finish before draining")
        busy = false
        try await until { installed }
        try require(local.terminationAllowed, "A drained engine permits termination")

        var attempts = 0, resumed = false
        installed = false
        let remote = UpdateGate(audioBusy: { false }, prepare: { _ in attempts += 1; return false }, resume: { _ in resumed = true })
        remote.begin { installed = true }
        try await until { attempts > 0 }
        remote.cancel()
        try await until { !remote.waiting }
        try require(resumed && !installed && !remote.terminationAllowed, "Cancel must restore admission without installing")

        resumed = false
        let failed = UpdateGate(audioBusy: { false }, prepare: { _ in throw URLError(.timedOut) }, resume: { _ in resumed = true })
        failed.begin { installed = true }
        try await until { !failed.waiting }
        try require(resumed && !installed && failed.failure != nil, "Failed handshake must never terminate")

        var installations = 0
        let repeated = UpdateGate(audioBusy: { false }, prepare: { _ in true }, resume: { _ in })
        repeated.begin { installations += 1 }
        repeated.begin { installations += 1 }
        try await until { installations > 0 }
        try require(installations == 1, "Repeated actions must not install twice")
        print("UpdateGate: 4 behavioral scenarios passed")
    }
}
