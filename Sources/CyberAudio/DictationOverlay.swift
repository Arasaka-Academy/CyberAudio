import AppKit
import SwiftUI

@MainActor
final class DictationOverlay {
    private final class Panel: NSPanel {
        override var canBecomeKey: Bool { false }
        override var canBecomeMain: Bool { false }
    }
    private var panel: Panel?
    func show(title: String, detail: String, levels: [Float], seconds: Int?, cancel: (() -> Void)?, copy: (() -> Void)?) {
        if panel == nil {
            let panel = Panel(contentRect: NSRect(x: 0, y: 0, width: 320, height: 82),
                              styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            panel.isFloatingPanel = true; panel.level = .floating
            panel.identifier = NSUserInterfaceItemIdentifier("dictation-overlay")
            panel.title = "CyberAudio 听写"
            panel.isOpaque = false; panel.backgroundColor = .clear
            panel.hidesOnDeactivate = false
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
            panel.isReleasedWhenClosed = false
            self.panel = panel
        }
        guard let panel else { return }
        panel.contentView = NSHostingView(rootView: OverlayContent(title: title, detail: detail, levels: levels, seconds: seconds, cancel: cancel, copy: copy))
        if !panel.isVisible {
            let screen = NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) } ?? NSScreen.main
            if let frame = screen?.visibleFrame { panel.setFrameOrigin(NSPoint(x: frame.midX - 160, y: frame.minY + 24)) }
            panel.orderFrontRegardless()
        }
    }
    func hide() { panel?.orderOut(nil) }
}

private struct OverlayContent: View {
    let title: String
    let detail: String
    let levels: [Float]
    let seconds: Int?
    let cancel: (() -> Void)?
    let copy: (() -> Void)?
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: seconds == nil ? "waveform" : "mic.fill")
                .font(.title3).foregroundStyle(seconds == nil ? StudioStyle.accent : .red)
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(title).font(.callout.weight(.semibold)).lineLimit(1)
                    if let seconds { Text(String(format: "%02d:%02d", seconds / 60, seconds % 60)).font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
                }
                if levels.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                else { CompactWaveform(levels: levels, color: .red).frame(height: 16) }
            }.frame(maxWidth: .infinity, alignment: .leading)
            if let copy { Button("复制", action: copy).buttonStyle(.borderless) }
            if let cancel {
                Button(action: cancel) { Image(systemName: "xmark") }.buttonStyle(.borderless).accessibilityLabel("取消听写")
            }
        }.padding(16).frame(width: 320, height: 82)
            .studioGlass(in: RoundedRectangle(cornerRadius: StudioStyle.corner))
    }
}
