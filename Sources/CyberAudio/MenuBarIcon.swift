import AppKit
import SwiftUI

struct MenuBarIcon: View {
    @Environment(\.colorScheme) private var colorScheme
    let busy: Bool

    var body: some View {
        icon
            .renderingMode(colorScheme == .light ? .original : .template)
            .resizable()
            .frame(width: 18, height: 18)
    }

    private var icon: Image {
        let name = busy ? "MenuBarActive" : "MenuBarIdle"
        let symbol = busy ? "waveform" : "waveform.circle"
        guard let source = Bundle.main.image(forResource: name)
                ?? NSImage(systemSymbolName: symbol, accessibilityDescription: nil) else {
            return Image(systemName: symbol)
        }
        guard colorScheme == .light else { return Image(nsImage: source) }

        // Bake white into the existing alpha mask: template rendering would let
        // the menu bar replace the requested light-theme color with black.
        let white = NSImage(size: source.size, flipped: false) { rect in
            source.draw(in: rect)
            NSColor.white.setFill()
            rect.fill(using: .sourceIn)
            return true
        }
        white.isTemplate = false
        return Image(nsImage: white)
    }
}
