import Metal
import SwiftUI
import Waveform

struct AudioWaveformView: View {
    let samples: [Float]
    var color: Color = StudioStyle.accent
    var progress: Double? = nil
    @ViewState private var buffer = SampleBuffer(samples: [0, 0, 0, 0])
    private static let supportsMetal = MTLCreateSystemDefaultDevice() != nil

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Rectangle().fill(color.opacity(0.18)).frame(height: 1)
                if Self.supportsMetal {
                    Waveform(samples: buffer).foregroundColor(color)
                } else {
                    // Remote desktops without a Metal device still show the same
                    // measured envelope, with no animation or synthetic samples.
                    Canvas { context, size in
                        guard samples.count >= 2 else { return }
                        var path = Path()
                        for index in stride(from: 0, to: samples.count - 1, by: 2) {
                            let x = CGFloat(index) / CGFloat(samples.count) * size.width
                            path.move(to: CGPoint(x: x, y: size.height * (1 - CGFloat(samples[index])) / 2))
                            path.addLine(to: CGPoint(x: x, y: size.height * (1 - CGFloat(samples[index + 1])) / 2))
                        }
                        context.stroke(path, with: .color(color), lineWidth: 1)
                    }
                }
                if let progress {
                    Capsule().fill(.primary.opacity(0.65)).frame(width: 1, height: geometry.size.height)
                        .offset(x: max(0, min(1, progress)) * max(0, geometry.size.width - 1))
                }
            }
        }
        .onChange(of: samples, initial: true) { _, values in
            // Waveform's reduction tree needs more than two samples.
            buffer = SampleBuffer(samples: values.count > 2 ? values : values + [Float](repeating: 0, count: 4 - values.count))
        }
        .accessibilityHidden(true)
    }
}

struct SpeechPlaybackOverview: View {
    @EnvironmentObject private var audio: AudioWorkspace

    private func time(_ seconds: Double) -> String {
        let value = max(0, Int(seconds))
        return String(format: "%d:%02d", value / 60, value % 60)
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: audio.playing ? "speaker.wave.2" : "waveform").foregroundStyle(.secondary).frame(width: 18)
            AudioWaveformView(samples: audio.speechWaveform.samples,
                              progress: audio.playing && audio.speechWaveform.duration > 0 ? audio.playbackSeconds / audio.speechWaveform.duration : nil)
                .frame(height: 28)
            Group {
                if audio.speechWaveform.duration > 0 {
                    Text(audio.playing ? "\(time(audio.playbackSeconds)) / \(time(audio.speechWaveform.duration))" : time(audio.speechWaveform.duration))
                        .monospacedDigit()
                } else { Text(audio.generating ? "等待音频…" : "等待生成") }
            }.font(.caption).foregroundStyle(.secondary).fixedSize()
        }
        .padding(.horizontal, 12).padding(.vertical, 9).contentSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(audio.generating ? "正在合成语音" : "合成音频")
        .accessibilityValue(audio.speechWaveform.duration > 0 ? "\(time(audio.playbackSeconds))，共 \(time(audio.speechWaveform.duration))" : "尚无音频")
        .accessibilityIdentifier("speech-waveform")
    }
}
