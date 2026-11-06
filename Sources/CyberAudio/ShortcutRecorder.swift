import AppKit
import Carbon.HIToolbox
import SwiftUI

struct ShortcutRecorder: NSViewRepresentable {
    var capture: (DictationShortcut) -> Void
    var cancel: () -> Void
    var error: (String) -> Void
    func makeNSView(context: Context) -> CaptureView {
        let view = CaptureView()
        view.capture = capture; view.cancel = cancel; view.reportError = error
        view.setAccessibilityLabel("按下听写快捷键")
        return view
    }
    func updateNSView(_ nsView: CaptureView, context: Context) {}

    final class CaptureView: NSView {
        var capture: ((DictationShortcut) -> Void)?
        var cancel: (() -> Void)?
        var reportError: ((String) -> Void)?
        private var rightOptionCandidate = false
        override var acceptsFirstResponder: Bool { true }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            DispatchQueue.main.async { [weak self] in if let self { self.window?.makeFirstResponder(self) } }
        }
        override func performKeyEquivalent(with event: NSEvent) -> Bool { keyDown(with: event); return true }
        override func flagsChanged(with event: NSEvent) {
            let flags = UInt64(event.modifierFlags.rawValue)
            if event.keyCode == 61, flags & 0x40 == 0, rightOptionCandidate {
                rightOptionCandidate = false
                capture?(.rightOption)
            } else {
                rightOptionCandidate = event.keyCode == 61 && flags & DictationShortcut.modifierMask == DictationShortcut.option && flags & 0x20 == 0
            }
        }
        override func keyDown(with event: NSEvent) {
            rightOptionCandidate = false
            if event.keyCode == 53 { cancel?(); return }
            guard !event.isARepeat else { return }
            let names: [UInt16: String] = [49: "空格", 36: "↩", 48: "⇥", 51: "⌫", 123: "←", 124: "→", 125: "↓", 126: "↑"]
            let label = names[event.keyCode] ?? event.charactersIgnoringModifiers?.uppercased() ?? ""
            let shortcut = DictationShortcut(kind: .combination, keyCode: event.keyCode,
                                            modifiers: UInt64(event.modifierFlags.rawValue) & DictationShortcut.modifierMask, keyLabel: label)
            guard shortcut.valid else { reportError?("请使用 ⌃、⌥ 或 ⌘ 加一个按键。"); return }
            guard !reserved(shortcut) else { reportError?("该组合键已用于系统或常用编辑操作，请换一个。"); return }
            capture?(shortcut)
        }
        private func reserved(_ shortcut: DictationShortcut) -> Bool {
            if shortcut.modifiers & ~(DictationShortcut.command | DictationShortcut.shift) == 0,
               [0, 6, 7, 8, 9, 12, 13, 29, 36, 43, 47].contains(shortcut.keyCode) { return true }
            var raw: Unmanaged<CFArray>?
            guard CopySymbolicHotKeys(&raw) == noErr, let keys = raw?.takeRetainedValue() as? [[String: Any]] else { return false }
            var flags: UInt32 = 0
            for (modern, legacy) in [(DictationShortcut.command, cmdKey), (DictationShortcut.shift, shiftKey),
                                     (DictationShortcut.option, optionKey), (DictationShortcut.control, controlKey)] {
                if shortcut.modifiers & modern != 0 { flags |= UInt32(legacy) }
            }
            return keys.contains {
                ($0[kHISymbolicHotKeyEnabled as String] as? Bool == true)
                && ($0[kHISymbolicHotKeyCode as String] as? NSNumber)?.uint16Value == shortcut.keyCode
                && ($0[kHISymbolicHotKeyModifiers as String] as? NSNumber)?.uint32Value == flags
            }
        }
    }
}
