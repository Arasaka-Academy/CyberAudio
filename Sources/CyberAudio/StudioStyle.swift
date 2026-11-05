import SwiftUI

enum StudioStyle {
    static let accent = Color.teal
    static let corner: CGFloat = 26
}

struct WorkspaceBackground: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor)
            if !reduceTransparency {
                RadialGradient(colors: [.teal.opacity(scheme == .dark ? 0.12 : 0.075), .clear],
                               center: .topTrailing, startRadius: 0, endRadius: 620)
                RadialGradient(colors: [.cyan.opacity(0.045), .clear],
                               center: .bottomLeading, startRadius: 0, endRadius: 480)
            }
        }.ignoresSafeArea().allowsHitTesting(false)
    }
}

private struct GlassSurface<S: Shape>: ViewModifier {
    let shape: S
    let tint: Color?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    @ViewBuilder func body(content: Content) -> some View {
        if reduceTransparency {
            content.background(Color(nsColor: .controlBackgroundColor), in: shape)
                .overlay(shape.stroke(.primary.opacity(contrast == .increased ? 0.5 : 0.15), lineWidth: 1).allowsHitTesting(false))
        } else {
            content.glassEffect(.regular.tint(tint), in: shape)
                .overlay(shape.stroke(.primary.opacity(contrast == .increased ? 0.5 : 0), lineWidth: 1).allowsHitTesting(false))
        }
    }
}

private struct StudioButton: ViewModifier {
    let prominent: Bool
    let tint: Color
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @ViewBuilder func body(content: Content) -> some View {
        if reduceTransparency {
            if prominent { content.buttonStyle(.borderedProminent).tint(tint) }
            else { content.buttonStyle(.bordered).tint(tint) }
        } else {
            if prominent { content.buttonStyle(.glassProminent).tint(tint) }
            else { content.buttonStyle(.glass).tint(tint) }
        }
    }
}

private struct ContentSurface: ViewModifier {
    var editor = false
    @Environment(\.colorSchemeContrast) private var contrast

    func body(content: Content) -> some View {
        content
            .background(Color(nsColor: editor ? .textBackgroundColor : .controlBackgroundColor),
                        in: .rect(cornerRadius: StudioStyle.corner))
            .overlay {
                RoundedRectangle(cornerRadius: StudioStyle.corner)
                    .stroke(.primary.opacity(contrast == .increased ? 0.4 : 0.065), lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

extension View {
    func studioGlass<S: Shape>(in shape: S, tint: Color? = nil) -> some View {
        modifier(GlassSurface(shape: shape, tint: tint))
    }

    func studioButton(prominent: Bool = false, tint: Color = StudioStyle.accent) -> some View {
        modifier(StudioButton(prominent: prominent, tint: tint))
    }

    func contentSurface(editor: Bool = false) -> some View {
        modifier(ContentSurface(editor: editor))
    }
}

struct EngineStatus: View {
    @EnvironmentObject private var backend: BackendController

    private var color: Color {
        if backend.status == "failed" { return .orange }
        if backend.busy || backend.status == "loading" { return .orange }
        return backend.ready ? StudioStyle.accent : .secondary
    }

    var body: some View {
        HStack(spacing: 7) {
            if backend.status == "loading" || backend.status == "stopping" {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: backend.ready ? "checkmark.circle.fill" : "circle.dotted")
                    .foregroundStyle(color)
            }
            Text(backend.statusLabel).font(.system(size: 12, weight: .medium))
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .studioGlass(in: Capsule())
        .help(backend.detail)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("语音引擎，\(backend.statusLabel)")
    }
}

struct InlineNotice: View {
    let message: String
    var dismiss: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange)
            Text(message).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let dismiss {
                Button(action: dismiss) { Image(systemName: "xmark") }
                    .buttonStyle(.plain).help("关闭提示").accessibilityLabel("关闭错误提示")
            }
        }
        .padding(14)
        .background(.orange.opacity(0.09), in: .rect(cornerRadius: 14))
        .accessibilityElement(children: .contain)
    }
}

struct AudioVisual: View {
    let symbol: String
    var active = false
    var recording = false
    var levels: [Float] = []
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var color: Color { recording ? .red : StudioStyle.accent }

    var body: some View {
        ZStack {
            Circle().fill(color.opacity(0.045)).frame(width: 174, height: 174)
            Circle().stroke(color.opacity(0.1), lineWidth: 1).frame(width: 146, height: 146)
            Circle().stroke(color.opacity(0.08), lineWidth: 1).frame(width: 174, height: 174)
            Image(systemName: symbol)
                .font(.system(size: 35, weight: .medium))
                .foregroundStyle(color.gradient)
                .symbolEffect(.pulse, options: .repeating, isActive: active && !reduceMotion)
                .frame(width: 94, height: 94)
                .studioGlass(in: Circle(), tint: color.opacity(0.08))
            if !levels.isEmpty {
                RecordingWaveform(levels: levels, color: color)
                    .frame(width: 192, height: 30).offset(y: 98)
            }
        }
        .frame(width: 216, height: levels.isEmpty ? 190 : 220)
        .accessibilityHidden(true)
    }
}

private struct RecordingWaveform: View {
    let levels: [Float]
    let color: Color

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 2) {
                ForEach(levels.indices, id: \.self) { index in
                    Capsule().fill(color.opacity(levels[index] > 0 ? 0.75 : 0.2))
                        .frame(height: max(3, CGFloat(levels[index]) * geometry.size.height))
                }
            }.frame(height: geometry.size.height)
        }
    }
}

struct WorkspaceFooter: View {
    let detail: String

    var body: some View {
        HStack(spacing: 8) {
            Label(detail, systemImage: "lock.shield")
            Spacer(minLength: 12)
            Text("本地处理 · Apple GPU").font(.caption.monospaced())
        }
        .font(.caption).foregroundStyle(.secondary)
        .padding(.horizontal, 4)
        .accessibilityElement(children: .combine)
    }
}
