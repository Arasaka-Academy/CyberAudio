import Foundation

struct DictationShortcut: Codable, Equatable {
    enum Kind: String, Codable { case rightOption, combination }
    var kind: Kind = .rightOption
    var keyCode: UInt16 = 61
    var modifiers: UInt64 = 0
    var keyLabel = ""
    static let rightOption = DictationShortcut()
    static let shift: UInt64 = 1 << 17
    static let control: UInt64 = 1 << 18
    static let option: UInt64 = 1 << 19
    static let command: UInt64 = 1 << 20
    static let modifierMask = shift | control | option | command
    var display: String {
        if kind == .rightOption { return "右 ⌥" }
        return [(Self.control, "⌃"), (Self.option, "⌥"), (Self.shift, "⇧"), (Self.command, "⌘")]
            .filter { modifiers & $0.0 != 0 }.map(\.1).joined() + keyLabel
    }
    var valid: Bool {
        if kind == .rightOption { return keyCode == 61 && modifiers == 0 }
        return modifiers & (Self.control | Self.option | Self.command) != 0 && modifiers & ~Self.modifierMask == 0
            && !keyLabel.isEmpty && keyCode < 128 && ![53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63].contains(keyCode)
    }
}

struct ShortcutMatcher {
    enum Input: Equatable { case press, release, interrupt }
    enum Hardware { case down(UInt16, UInt64, Bool), up(UInt16), flags(UInt16, UInt64, Bool) }
    var shortcut: DictationShortcut = .rightOption
    private(set) var held = false
    mutating func handle(_ event: Hardware) -> Input? {
        switch (shortcut.kind, event) {
        case (.rightOption, .flags(61, let flags, let down)):
            if !down { return release() }
            guard !held, flags & DictationShortcut.modifierMask == DictationShortcut.option, flags & 0x20 == 0 else { return nil }
            held = true
            return .press
        case (.rightOption, .flags): return held ? .interrupt : nil
        case (.rightOption, .down): return held ? .interrupt : nil
        case (.combination, .down(let code, let flags, let repeated)):
            if code == shortcut.keyCode, flags & DictationShortcut.modifierMask == shortcut.modifiers {
                guard !repeated, !held else { return nil }
                held = true
                return .press
            }
            return held ? .interrupt : nil
        case (.combination, .up(let code)):
            return code == shortcut.keyCode ? release() : nil
        case (.combination, .flags(_, let flags, _)):
            guard held else { return nil }
            let modifiers = flags & DictationShortcut.modifierMask
            if modifiers & shortcut.modifiers != shortcut.modifiers { return release() }
            return modifiers != shortcut.modifiers ? .interrupt : nil
        default: return nil
        }
    }
    private mutating func release() -> Input? {
        guard held else { return nil }
        held = false
        return .release
    }
    mutating func reconcile(physicallyHeld: Bool) -> Input? {
        physicallyHeld ? nil : release()
    }
}
