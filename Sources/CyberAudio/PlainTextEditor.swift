import AppKit
import SwiftUI

/// A wrapping native editor with scrolling, but no scrollers even under "Always".
struct PlainTextEditor: NSViewRepresentable {
    @Binding var text: String
    var editable = true
    var label: String

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = false
        scroll.hasHorizontalScroller = false
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        let editor = NSTextView(frame: .zero)
        editor.isRichText = false
        editor.importsGraphics = false
        editor.allowsUndo = true
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = false
        editor.autoresizingMask = [.width]
        editor.textContainer?.widthTracksTextView = true
        editor.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainerInset = NSSize(width: 4, height: 6)
        editor.drawsBackground = false
        editor.font = .systemFont(ofSize: 15)
        editor.delegate = context.coordinator
        editor.setAccessibilityLabel(label)
        scroll.documentView = editor
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let editor = scroll.documentView as? NSTextView else { return }
        editor.isEditable = editable
        editor.isSelectable = true
        editor.textColor = .labelColor
        editor.insertionPointColor = .labelColor
        editor.setAccessibilityLabel(label)
        // Never replace an IME's marked text or reset the caret on each keystroke.
        if editor.string != text, !editor.hasMarkedText() {
            let selection = editor.selectedRange()
            context.coordinator.updating = true
            editor.string = text
            let length = (text as NSString).length
            let location = min(selection.location, length)
            editor.setSelectedRange(NSRange(location: location, length: min(selection.length, length - location)))
            editor.undoManager?.removeAllActions()
            context.coordinator.updating = false
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: PlainTextEditor
        var updating = false
        init(_ parent: PlainTextEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard !updating, let editor = notification.object as? NSTextView else { return }
            parent.text = editor.string
        }
    }
}

struct CompactWindowSizing: NSViewRepresentable {
    func makeNSView(context: Context) -> WindowAnchor { WindowAnchor() }
    func updateNSView(_ nsView: WindowAnchor, context: Context) {}

    final class WindowAnchor: NSView {
        private var applied = false
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window, !applied else { return }
            applied = true
            DispatchQueue.main.async {
                let defaults = UserDefaults.standard
                let migrated = defaults.bool(forKey: "CompactWorkspaceWindowV1")
                window.contentMinSize = NSSize(width: 560, height: 440)
                if !migrated || !window.setFrameUsingName("CyberAudio.CompactMain") {
                    window.setContentSize(NSSize(width: 560, height: 440))
                    window.center()
                }
                window.setFrameAutosaveName("CyberAudio.CompactMain")
                defaults.set(true, forKey: "CompactWorkspaceWindowV1")
            }
        }
    }
}
