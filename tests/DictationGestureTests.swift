import Foundation

@main
struct DictationGestureTests {
    struct Failure: Error { let message: String }
    static func expect(_ actual: [DictationGesture.Action], _ expected: [DictationGesture.Action], _ message: String) throws {
        if actual != expected { throw Failure(message: "\(message): \(actual) != \(expected)") }
    }
    static func main() throws {
        var matcher = ShortcutMatcher()
        try require(matcher.handle(.flags(58, 1 << 19, false)) == nil, "Left Option alone must never record")
        try require(matcher.handle(.flags(61, 1 << 19, true)) == .press, "Physical right Option starts a hold")
        try require(matcher.handle(.flags(58, (1 << 19) | 0x20, true)) == .interrupt, "Adding left Option cancels the hold")
        try require(matcher.handle(.flags(61, (1 << 19) | 0x20, false)) == .release, "Releasing right Option is detected while left Option remains down")
        matcher.shortcut = DictationShortcut(kind: .combination, keyCode: 49, modifiers: (1 << 18) | (1 << 19), keyLabel: "空格")
        try require(matcher.handle(.down(49, (1 << 18) | (1 << 19), false)) == .press, "The configured chord starts")
        try require(matcher.handle(.down(49, (1 << 18) | (1 << 19), true)) == nil, "Key repeat cannot start another hold")
        try require(matcher.handle(.flags(59, 1 << 19, false)) == .release, "Releasing a chord modifier ends the hold")
        try require(matcher.handle(.up(49)) == nil, "The later key-up cannot submit twice")
        try require(matcher.handle(.down(49, (1 << 18) | (1 << 19), false)) == .press, "A later chord starts another hold")
        try require(matcher.reconcile(physicallyHeld: true) == nil, "Hardware polling observes the down state before recovering a lost release")
        try require(matcher.reconcile(physicallyHeld: false) == .release, "Hardware polling recovers a lost key-up")
        try require(matcher.reconcile(physicallyHeld: false) == nil, "A recovered release is delivered exactly once")
        try require(matcher.handle(.down(49, (1 << 18) | (1 << 19), true)) == nil, "Repeat after a lost release cannot rearm")
        var remote = ShortcutMatcher()
        var remoteHold = DictationGesture()
        try require(remote.handle(.flags(61, 0x80040, true)) == .press, "A session keyboard event starts a hold")
        _ = remoteHold.press(at: 1)
        try require(remote.reconcile(physicallyHeld: false) == nil, "An unobserved HID press must not erase a valid session key-down before the hold threshold")
        try expect(remoteHold.advance(to: 1.16), [.record], "A keyboard source without HID state can still start dictation")
        try require(remote.handle(.flags(61, 0, false)) == .release, "The session key-up ends a hold without HID state")
        try expect(remoteHold.release(), [.submit], "A session key-up submits once")
        var hold = DictationGesture()
        try expect(hold.press(at: 1), [.arm], "A new press captures one target")
        try expect(hold.advance(to: 1.1), [], "A short hold must not record")
        try expect(hold.release(), [.cancel], "A tap must not submit audio")
        try expect(hold.advance(to: 2), [], "A released tap cannot start late")

        try expect(hold.press(at: 3), [.arm], "A second press arms")
        try expect(hold.press(at: 3.1), [], "Repeated key-down cannot arm twice")
        try expect(hold.advance(to: 3.16), [.record], "Long hold starts exactly once")
        try expect(hold.advance(to: 4), [], "Further ticks cannot restart recording")
        try expect(hold.release(), [.submit], "Release submits exactly once")
        try expect(hold.release(), [], "Duplicate release must not submit twice")

        _ = hold.press(at: 5)
        _ = hold.advance(to: 5.2)
        try expect(hold.interrupt(), [.cancel], "Option used in a chord cancels recording")
        try expect(hold.press(at: 6), [], "Interrupted hold requires release before rearming")
        try expect(hold.release(), [], "Interrupted hold must never submit")

        _ = hold.press(at: 7)
        _ = hold.advance(to: 7.2)
        try expect(hold.limitReached(), [.submit], "The recording limit submits")
        try expect(hold.limitReached(), [], "The limit submits only once")
        try expect(hold.press(at: 8), [], "Limit cannot rearm while the key stays held")
        try expect(hold.release(), [], "Release after the limit must not submit again")

        _ = hold.press(at: 9)
        try expect(hold.interrupt(), [.cancel], "Interrupt during arming cancels the pending start")
        try expect(hold.advance(to: 10), [], "An interrupted timer cannot record")
        hold.reset()
        try expect(hold.press(at: 11), [.arm], "Lifecycle reset permits a fresh press")

        var session = DictationSession()
        let first = session.begin()
        session.invalidateTarget()
        try require(!session.canInsert(first), "Focus invalidation must be sticky")
        let second = session.begin()
        try require(!session.canInsert(first) && session.canInsert(second), "Late results cannot insert into another session")
        session.finish(second)
        try require(!session.canInsert(second), "A completed session cannot insert twice")
        print("DictationGesture: hold, hardware matching, lost release, cancellation, limit and stale-session checks passed")
    }
    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw Failure(message: message) }
    }
}
