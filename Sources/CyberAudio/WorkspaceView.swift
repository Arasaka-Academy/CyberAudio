import AppKit
import SwiftUI

enum WorkspaceMode: String, CaseIterable, Identifiable {
    case transcription = "语音转文字"
    case speech = "文字转语音"
    var id: Self { self }
}

@MainActor
private final class WorkspaceNavigation: ObservableObject {
    @Published var mode = WorkspaceMode.transcription
    @Published var instructionsExpanded = false
}

struct ContentView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var updater: UpdaterController
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var navigation = WorkspaceNavigation()

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 20) {
                    if !backend.ready { engineNotice }
                    Group {
                        switch navigation.mode {
                        case .transcription: TranscriptionWorkspace()
                        case .speech: SpeechWorkspace(instructionsExpanded: $navigation.instructionsExpanded)
                        }
                    }
                    .id(navigation.mode)
                    .transition(.opacity)
                }
                .frame(maxWidth: 1120)
                .padding(geometry.size.width < 900 ? 24 : 32)
                .frame(maxWidth: .infinity)
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 8) {
                    if updater.gate.waiting { UpdateWaitingBar() }
                    if audio.working || audio.playing { ActiveAudioBar(mode: $navigation.mode) }
                }
                .padding(.horizontal, 24).padding(.bottom, 16).padding(.top, 8)
            }
        }
        .background { WorkspaceBackground() }
        // The unified toolbar adds 54 points to the window's content minimum.
        .frame(minWidth: 820, minHeight: 586)
        .tint(StudioStyle.accent)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: navigation.mode)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("工作模式", selection: $navigation.mode) {
                    ForEach(WorkspaceMode.allCases) { item in
                        Text(item.rawValue).tag(item)
                    }
                }
                .pickerStyle(.segmented).frame(width: 280)
                .accessibilityIdentifier("workspace-mode")
            }
            ToolbarItem(placement: .primaryAction) { EngineStatus() }
            if updater.availableVersion != nil || updater.gate.waiting {
                ToolbarItem(placement: .primaryAction) {
                    Button { updater.checkForUpdates() } label: { Image(systemName: "arrow.down.circle") }
                        .help(updater.menuTitle).accessibilityLabel(updater.menuTitle)
                        .disabled(!updater.canPerformAction)
                }
            }
            ToolbarItem(placement: .primaryAction) {
                SettingsLink { Image(systemName: "gearshape") }
                    .help("设置 (⌘,)").accessibilityLabel("打开设置")
            }
        }
    }

    private var engineNotice: some View {
        HStack(spacing: 12) {
            if backend.status == "loading" || backend.status == "stopping" { ProgressView().controlSize(.small) }
            else { Image(systemName: "exclamationmark.circle").foregroundStyle(.orange) }
            VStack(alignment: .leading, spacing: 3) {
                Text(backend.statusLabel).font(.callout.weight(.semibold))
                Text(backend.detail).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer(minLength: 8)
            SettingsLink { Text("查看设置") }.studioButton()
        }
        .padding(16).contentSurface()
    }
}

private struct TranscriptionWorkspace: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace

    private var title: String {
        if audio.requestingMicrophone { return "正在开启麦克风" }
        if audio.recording { return audio.recordingSeconds >= 59 ? "录音已达上限" : "正在聆听" }
        if audio.transcribing { return "正在识别语音" }
        return "让声音，成为文字。"
    }

    private var subtitle: String {
        if audio.recording { return audio.recordingSeconds >= 59 ? "点击停止，将这段录音转成文字。" : "说完后，点击停止并识别。" }
        if audio.transcribing { return "正在本机处理，识别结果即将显示在下方。" }
        return "录下此刻的想法，或导入一段音频。"
    }

    var body: some View {
        VStack(spacing: 20) {
            VStack(spacing: 0) {
                HStack(spacing: 30) {
                    AudioVisual(symbol: audio.recording ? "mic.fill" : "mic", active: audio.recording,
                                recording: audio.recording, levels: audio.recordingLevels)
                    VStack(alignment: .leading, spacing: 14) {
                        Label("语音转文字", systemImage: "waveform")
                            .font(.caption.weight(.semibold)).foregroundStyle(StudioStyle.accent)
                        Text(title).font(.system(size: 29, weight: .semibold)).tracking(-0.6)
                        Text(subtitle).font(.callout).foregroundStyle(.secondary)
                        GlassEffectContainer(spacing: 14) {
                            HStack(spacing: 12) {
                                Button {
                                    Task {
                                        if audio.recording { await audio.stopAndTranscribe(using: backend) }
                                        else { await audio.beginRecording() }
                                    }
                                } label: {
                                    Label(audio.recording ? "停止并识别" : "开始录音", systemImage: audio.recording ? "stop.fill" : "mic.fill")
                                        .padding(.horizontal, 8)
                                }
                                .studioButton(prominent: true, tint: audio.recording ? .red : StudioStyle.accent)
                                .disabled(!audio.recording && (!backend.ready || backend.busy || audio.cannotStart))
                                .help("开始录音／停止并识别 (⇧⌘R)")
                                Button("导入音频…", systemImage: "square.and.arrow.down") { audio.importAudio(using: backend) }
                                    .studioButton().disabled(!backend.ready || backend.busy || audio.cannotStart)
                            }.controlSize(.large)
                        }.padding(.top, 6)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.padding(.horizontal, 28).padding(.vertical, 16)
                Divider().overlay(.primary.opacity(0.025)).padding(.horizontal, 24)
                HStack(spacing: 16) {
                    Picker("识别语言", selection: $audio.sttLanguage) {
                        Text("自动识别").tag("auto")
                        Text("中文").tag("zh")
                        Text("English").tag("en")
                    }.frame(width: 200).disabled(audio.cannotStart)
                    Spacer()
                    if audio.recording {
                        Label(String(format: "%02d:%02d", audio.recordingSeconds / 60, audio.recordingSeconds % 60), systemImage: "record.circle")
                            .font(.callout.monospacedDigit()).foregroundStyle(.red)
                            .accessibilityLabel("录音 \(audio.recordingSeconds) 秒")
                    } else {
                        Text("最长 60 秒 · 20MiB").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.horizontal, 26).padding(.vertical, 16)
            }.contentSurface()

            if let message = audio.errorMessage, audio.errorOperation == .transcription {
                InlineNotice(message: message) { audio.errorMessage = nil }
            }
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    Label("识别结果", systemImage: "text.alignleft").font(.headline)
                    if audio.transcribing { ProgressView().controlSize(.mini) }
                    Spacer()
                    Button("复制", systemImage: "doc.on.doc") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(audio.transcript, forType: .string)
                    }.disabled(audio.transcript.isEmpty).studioButton()
                    Button("清空", systemImage: "trash") { audio.transcript = ""; audio.sttTiming = "" }
                        .disabled(audio.transcript.isEmpty || audio.transcribing).studioButton()
                }
                ZStack(alignment: .topLeading) {
                    TextEditor(text: $audio.transcript)
                        .font(.system(size: 17)).lineSpacing(6)
                        .scrollContentBackground(.hidden)
                        .accessibilityLabel("识别结果").disabled(audio.transcribing)
                    if audio.transcript.isEmpty {
                        Text(audio.transcribing ? "正在将声音整理成文字…" : "识别完成后，文字会显示在这里。\n你可以继续编辑，或复制到其他应用。")
                            .font(.system(size: 15)).foregroundStyle(.secondary).lineSpacing(7)
                            .padding(.horizontal, 5).padding(.top, 1).allowsHitTesting(false)
                    }
                }.frame(minHeight: 160)
                if !audio.sttTiming.isEmpty {
                    Text(audio.sttTiming).font(.caption.monospacedDigit()).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }.padding(22).contentSurface(editor: true)
            WorkspaceFooter(detail: "录音临时处理，不写入历史记录")
        }
    }
}

private struct SpeechWorkspace: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @Binding var instructionsExpanded: Bool
    private let voices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]

    private var canGenerate: Bool {
        backend.ready && !backend.busy && !audio.cannotStart && !audio.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && audio.text.unicodeScalars.count <= 500 && audio.instructions.unicodeScalars.count <= 200
    }

    private var title: String {
        if audio.generating { return audio.playing ? "边生成，边播放。" : "正在生成语音" }
        if audio.playing { return "正在播放" }
        return audio.generated.isEmpty ? "赋予文字，自然的声音。" : "语音已准备好"
    }

    var body: some View {
        VStack(spacing: 20) {
            VStack(spacing: 0) {
                HStack(spacing: 30) {
                    AudioVisual(symbol: audio.playing ? "speaker.wave.2.fill" : "speaker.wave.2", active: audio.playing || audio.generating)
                    VStack(alignment: .leading, spacing: 14) {
                        Label("文字转语音", systemImage: "waveform")
                            .font(.caption.weight(.semibold)).foregroundStyle(StudioStyle.accent)
                        Text(title).font(.system(size: 29, weight: .semibold)).tracking(-0.6)
                        Text(audio.generating ? "语音正在本机生成，随时可以取消。" : "选择一个声音，让文字在这台 Mac 上被听见。")
                            .font(.callout).foregroundStyle(.secondary)
                        GlassEffectContainer(spacing: 14) {
                            HStack(spacing: 12) {
                                if audio.generating {
                                    Button("取消生成", systemImage: "stop.fill") { audio.cancelGeneration() }.studioButton(prominent: true)
                                } else {
                                    Button("生成并播放", systemImage: "play.fill") { audio.generate(using: backend) }
                                        .studioButton(prominent: true).disabled(!canGenerate)
                                        .keyboardShortcut(.return, modifiers: .command)
                                }
                                if audio.playing && !audio.generating {
                                    Button("停止播放", systemImage: "stop.fill") { audio.stopPlayback() }.studioButton()
                                } else if !audio.generating {
                                    Button("重播", systemImage: "arrow.counterclockwise") {
                                        do { try audio.playGenerated() } catch { audio.reportError(error, for: .speech) }
                                    }.studioButton().disabled(audio.generated.isEmpty || audio.working)
                                }
                            }.controlSize(.large)
                        }.padding(.top, 6)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.padding(.horizontal, 28).padding(.vertical, 16)
                Divider().padding(.horizontal, 24)
                HStack {
                    Toggle("流式播放", isOn: $audio.streaming).toggleStyle(.switch).controlSize(.small).disabled(audio.generating)
                        .help("开启后，语音生成过程中即可开始播放。")
                    Spacer()
                    Button("保存 WAV…", systemImage: "square.and.arrow.up") { audio.save() }
                        .studioButton().disabled(audio.generated.isEmpty || audio.generating)
                }.padding(.horizontal, 26).padding(.vertical, 16)
            }.contentSurface()

            if let message = audio.errorMessage, audio.errorOperation == .speech {
                InlineNotice(message: message) { audio.errorMessage = nil }
            }
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label("朗读文本", systemImage: "text.alignleft").font(.headline)
                    Spacer()
                    Text("\(audio.text.unicodeScalars.count) / 500")
                        .font(.caption.monospacedDigit()).foregroundStyle(audio.text.unicodeScalars.count > 500 ? .red : .secondary)
                }
                TextEditor(text: $audio.text).font(.system(size: 17)).lineSpacing(6)
                    .scrollContentBackground(.hidden).frame(minHeight: 130)
                    .accessibilityLabel("朗读文本").disabled(audio.generating)
                if audio.text.unicodeScalars.count > 500 {
                    Text("朗读文本最多 500 个字符，请缩短后再生成。").font(.caption).foregroundStyle(.red)
                }
                Divider()
                HStack(spacing: 28) {
                    Picker("音色", selection: $audio.voice) {
                        ForEach(voices, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ").capitalized).tag($0) }
                    }
                    Picker("语言", selection: $audio.ttsLanguage) {
                        Text("自动").tag("auto"); Text("中文").tag("chinese"); Text("English").tag("english")
                    }
                }.disabled(audio.generating)
                DisclosureGroup("表达指令", isExpanded: $instructionsExpanded) {
                    VStack(alignment: .leading, spacing: 8) {
                        TextField("例如：自然、清晰、亲切地朗读。", text: $audio.instructions, axis: .vertical)
                            .textFieldStyle(.roundedBorder).lineLimit(2...4).disabled(audio.generating)
                        HStack {
                            Text("描述语气、节奏或情绪。")
                            Spacer()
                            Text("\(audio.instructions.unicodeScalars.count) / 200").monospacedDigit()
                        }.font(.caption).foregroundStyle(audio.instructions.unicodeScalars.count > 200 ? .red : .secondary)
                    }.padding(.top, 10)
                }
                if audio.instructions.unicodeScalars.count > 200 {
                    Text("表达指令最多 200 个字符，请缩短后再生成。").font(.caption).foregroundStyle(.red)
                }
            }.padding(22).contentSurface(editor: true)
            WorkspaceFooter(detail: audio.ttsTiming.isEmpty ? "24kHz · 单声道 · PCM 16-bit" : audio.ttsTiming)
        }
    }
}

private struct ActiveAudioBar: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @Binding var mode: WorkspaceMode

    private var taskMode: WorkspaceMode {
        audio.recording || audio.transcribing || audio.requestingMicrophone ? .transcription : .speech
    }

    private var title: String {
        if audio.requestingMicrophone { return "等待麦克风授权" }
        if audio.recording { return audio.recordingSeconds >= 59 ? "已达录音上限，请停止并识别" : "正在录音" }
        if audio.transcribing { return "正在识别语音" }
        if audio.generating { return audio.playing ? "正在生成并播放" : "正在生成语音" }
        return "正在播放语音"
    }

    var body: some View {
        HStack(spacing: 14) {
            if audio.transcribing || audio.generating || audio.requestingMicrophone {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: audio.recording ? "record.circle.fill" : "speaker.wave.2.fill")
                    .foregroundStyle(audio.recording ? .red : StudioStyle.accent)
            }
            Text(title).font(.callout.weight(.medium))
            if audio.recording {
                Text(String(format: "%02d:%02d", audio.recordingSeconds / 60, audio.recordingSeconds % 60))
                    .font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            if mode != taskMode {
                Button("查看任务") { mode = taskMode }.studioButton()
            }
            if audio.recording {
                Button("停止并识别", systemImage: "stop.fill") { Task { await audio.stopAndTranscribe(using: backend) } }
                    .studioButton(prominent: true, tint: .red)
            } else if audio.transcribing {
                Button("取消识别", systemImage: "stop.fill") { audio.cancelTranscription() }.studioButton()
            } else if audio.generating {
                Button("取消生成", systemImage: "stop.fill") { audio.cancelGeneration() }.studioButton()
            } else if audio.playing {
                Button("停止播放", systemImage: "stop.fill") { audio.stopPlayback() }.studioButton()
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 13)
        .studioGlass(in: RoundedRectangle(cornerRadius: 20))
        .frame(maxWidth: 1072)
        .accessibilityIdentifier("active-audio-controls")
    }
}
