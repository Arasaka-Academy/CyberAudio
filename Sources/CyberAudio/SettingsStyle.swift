import Luminare
import SwiftUI

/// Keep third-party styling at the presentation boundary. Native controls still
/// own focus, keyboard input and accessibility throughout settings.
struct SettingsSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: () -> Content
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    init(_ title: String, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.content = content
    }

    var body: some View {
        if reduceTransparency || contrast == .increased {
            VStack(alignment: .leading, spacing: 8) {
                heading
                rows
                    .background(Color(nsColor: .controlBackgroundColor), in: .rect(cornerRadius: 12))
                    .overlay { RoundedRectangle(cornerRadius: 12).stroke(.primary.opacity(0.4), lineWidth: 1) }
            }
        } else {
            LuminareSection(hasPadding: false, headerSpacing: 8, outerPadding: 0) {
                rows
            } header: {
                heading
            } footer: {
                EmptyView()
            }
            .luminareSectionLayout(.stacked)
            .luminareHasDividers(false)
            .luminareCornerRadius(12)
        }
    }

    private var heading: some View {
        Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            .padding(.horizontal, 2).accessibilityAddTraits(.isHeader)
    }

    private var rows: some View {
        VStack(alignment: .leading, spacing: 12, content: content)
            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
    }
}

struct SettingsToggle: View {
    let title: String
    @Binding var isOn: Bool
    var body: some View {
        LuminareToggle(title, isOn: $isOn)
            .luminareMinHeight(26).luminareHorizontalPadding(0)
            .font(.callout)
    }
}

struct SettingsPageHeader: View {
    let title: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 21, weight: .semibold)).accessibilityAddTraits(.isHeader)
            Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.bottom, 4)
    }
}

private struct SettingsButtonStyle: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorSchemeContrast) private var contrast

    @ViewBuilder func body(content: Content) -> some View {
        if reduceTransparency || contrast == .increased {
            content.buttonStyle(.bordered)
        } else {
            content.buttonStyle(.luminareCompact)
                .luminareMinHeight(28).luminareHorizontalPadding(10)
                .luminareAnimationFast(.easeInOut(duration: reduceMotion ? 0 : 0.1))
        }
    }
}

extension View {
    func settingsButton() -> some View { modifier(SettingsButtonStyle()) }
}
