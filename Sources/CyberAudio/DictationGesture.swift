import Foundation

/// Input-independent hold semantics. Hardware matching lives in ShortcutMonitor.
struct DictationGesture {
    enum Action: Equatable { case arm, record, submit, cancel }
    private enum State { case idle, armed(TimeInterval), recording, untilRelease }
    private var state = State.idle
    var isHeld: Bool { if case .idle = state { return false }; return true }

    mutating func press(at time: TimeInterval) -> [Action] {
        guard case .idle = state else { return [] }
        state = .armed(time)
        return [.arm]
    }
    mutating func advance(to time: TimeInterval) -> [Action] {
        guard case .armed(let start) = state, time - start >= 0.15 else { return [] }
        state = .recording
        return [.record]
    }
    mutating func release() -> [Action] {
        defer { state = .idle }
        switch state {
        case .recording: return [.submit]
        case .armed: return [.cancel]
        case .idle, .untilRelease: return []
        }
    }
    mutating func interrupt() -> [Action] {
        switch state {
        case .armed, .recording: state = .untilRelease; return [.cancel]
        case .idle, .untilRelease: return []
        }
    }
    mutating func limitReached() -> [Action] {
        guard case .recording = state else { return [] }
        state = .untilRelease
        return [.submit]
    }
    mutating func reset() { state = .idle }
}

struct DictationSession {
    private(set) var id: UUID?
    private var targetValid = false
    mutating func begin() -> UUID {
        let value = UUID()
        id = value
        targetValid = true
        return value
    }
    mutating func invalidateTarget() { targetValid = false }
    func canInsert(_ candidate: UUID) -> Bool { id == candidate && targetValid }
    mutating func finish(_ candidate: UUID) {
        if id == candidate { id = nil; targetValid = false }
    }
}
