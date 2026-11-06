import AppKit
import SwiftUI

@main
@MainActor
struct ShortcutRecorderTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }

    static func main() {
        do { try run() }
        catch { fputs("ShortcutRecorder FAIL: \(error)\n", stderr); exit(1) }
    }

    static func run() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        var shortcuts: [DictationShortcut] = []
        var errors: [String] = []
        var cancellations = 0
        let host = NSHostingView(rootView: ShortcutRecorder(capture: { shortcuts.append($0) },
            cancel: { cancellations += 1 }, error: { errors.append($0) }).frame(width: 320, height: 54))
        let sheet = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 200), styleMask: [.titled], backing: .buffered, defer: false)
        sheet.contentView = host
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        func send(_ type: NSEvent.EventType, code: UInt16, flags: NSEvent.ModifierFlags = [], text: String = "") {
            let event = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags,
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: sheet.windowNumber, context: nil,
                characters: text, charactersIgnoringModifiers: text, isARepeat: false, keyCode: code)!
            NSApp.sendEvent(event)
        }

        // SwiftUI can move first responder after the representable joins a sheet.
        // Shortcut recording must still consume the sheet's keys before key equivalents.
        sheet.makeFirstResponder(nil)
        send(.keyDown, code: 40, flags: [.control, .option, .shift], text: "K")
        try require(shortcuts.count == 1 && shortcuts[0].display == "⌃⌥⇧K", "The shortcut sheet must capture a chord after first responder moves away")

        send(.keyDown, code: 0, flags: [.command], text: "A")
        try require(shortcuts.count == 1 && errors.count == 1, "Reserved editing shortcuts must report an error instead of escaping to a menu")
        send(.keyDown, code: 53)
        try require(cancellations == 1, "Escape must cancel recording once even without recorder focus")

        send(.flagsChanged, code: 61, flags: NSEvent.ModifierFlags(rawValue: 0x80040))
        send(.flagsChanged, code: 61)
        try require(shortcuts.count == 2 && shortcuts.last == .rightOption, "The sheet must capture the right Option down and up pair")

        let other = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 200), styleMask: [.titled], backing: .buffered, defer: false)
        let unrelated = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [.control, .option, .shift],
            timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: other.windowNumber, context: nil,
            characters: "K", charactersIgnoringModifiers: "K", isARepeat: false, keyCode: 40)!
        NSApp.sendEvent(unrelated)
        try require(shortcuts.count == 2, "Recorder must not capture a sibling window's keys")

        sheet.contentView = nil
        send(.keyDown, code: 40, flags: [.control, .option, .shift], text: "K")
        try require(shortcuts.count == 2, "Detaching the recorder must stop keyboard capture")
        print("ShortcutRecorder: focus loss, reserved shortcuts, Escape, right Option, window scope and teardown passed")
    }
}
