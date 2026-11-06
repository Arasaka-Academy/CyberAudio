import AppKit
import SwiftUI

enum WorkspaceMode: String, CaseIterable, Identifiable {
    case transcription = "语音转文字"
    case speech = "文字转语音"
    var id: Self { self }
}

struct ContentView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var updater: UpdaterController
    @EnvironmentObject private var dictation: DictationController
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ViewState private var mode: WorkspaceMode = .transcription

    init(initialMode: WorkspaceMode = .transcription) { _mode = ViewState(wrappedValue: initialMode) }

    var body: some View {
        VStack(spacing: 12) {
            switch mode {
            case .transcription: TranscriptionWorkspace()
            case .speech: SpeechWorkspace()
            }
            WorkspaceStatus(mode: mode)
        }
        .padding(16)
        .background { WorkspaceBackground() }
        .background(CompactWindowSizing())
        .frame(minWidth: 560, minHeight: 440)
        .tint(StudioStyle.accent)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: mode)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("工作模式", selection: $mode) {
                    ForEach(WorkspaceMode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).frame(width: 224)
                .accessibilityIdentifier("workspace-mode")
            }
            ToolbarItem(placement: .primaryAction) { EngineStatus() }
            ToolbarItem(placement: .primaryAction) {
                SettingsLink { Image(systemName: "gearshape") }
                    .help("设置 (⌘,)").accessibilityLabel("打开设置")
            }
        }
    }
}

private struct TranscriptionWorkspace: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var dictation: DictationController

    private var recordingTitle: String {
        if dictation.blocksUpdate { return "取消听写" }
        if audio.recording { return "停止并识别" }
        if audio.transcribing { return "取消识别" }
        return "开始录音"
    }

    var body: some View {
        VStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Label("识别结果", systemImage: "text.alignleft").font(.callout.weight(.semibold))
                    Spacer()
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(audio.transcript, forType: .string) } label: {
                        Image(systemName: "doc.on.doc")
                    }.help("复制识别结果").accessibilityLabel("复制识别结果").disabled(audio.transcript.isEmpty)
                    Button { audio.transcript = ""; audio.sttTiming = "" } label: { Image(systemName: "trash") }
                        .help("清空识别结果").accessibilityLabel("清空识别结果")
                        .disabled(audio.transcript.isEmpty || audio.working || dictation.blocksUpdate)
                }.buttonStyle(.borderless)
                ZStack(alignment: .topLeading) {
                    PlainTextEditor(text: $audio.transcript, editable: !audio.transcribing, label: "识别结果")
                    if audio.transcript.isEmpty {
                        Text(audio.transcribing ? "正在本机识别语音…" : "录音或导入音频，文字会显示在这里。")
                            .font(.system(size: 15)).foregroundStyle(.tertiary)
                            .padding(.horizontal, 9).padding(.top, 6).allowsHitTesting(false)
                    }
                }
            }.padding(14).contentSurface(editor: true).frame(maxHeight: .infinity)

            HStack(spacing: 10) {
                if audio.recording {
                    CompactWaveform(levels: audio.recordingLevels, color: .red).frame(width: 116, height: 22)
                    Text(String(format: "%02d:%02d", audio.recordingSeconds / 60, audio.recordingSeconds % 60))
                        .font(.caption.monospacedDigit()).foregroundStyle(.red)
                } else {
                    Image(systemName: "keyboard").foregroundStyle(.secondary)
                    Text("按住 \(dictation.shortcut.display) 听写").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 4)
                Picker("识别语言", selection: $audio.sttLanguage) {
                    Text("自动识别").tag("auto"); Text("中文").tag("zh"); Text("English").tag("en")
                }.labelsHidden().frame(width: 110).disabled(audio.cannotStart || dictation.blocksUpdate)
            }.frame(height: 26)

            HStack(spacing: 10) {
                Button {
                    if dictation.blocksUpdate { dictation.cancel() }
                    else if audio.transcribing { audio.cancelTranscription() }
                    else {
                        Task {
                            if audio.recording { await audio.stopAndTranscribe(using: backend) }
                            else { await audio.beginRecording() }
                        }
                    }
                } label: {
                    Label(recordingTitle, systemImage: audio.recording || audio.transcribing || dictation.blocksUpdate ? "stop.fill" : "mic.fill")
                        .frame(minWidth: 120)
                }
                .studioButton(prominent: true, tint: audio.recording ? .red : StudioStyle.accent)
                .disabled(!dictation.blocksUpdate && !audio.recording && !audio.transcribing && (!backend.ready || backend.busy || audio.cannotStart))
                Button("导入音频…", systemImage: "square.and.arrow.down") { audio.importAudio(using: backend) }
                    .studioButton().disabled(!backend.ready || backend.busy || audio.cannotStart || dictation.blocksUpdate)
                Spacer(minLength: 0)
                Text("最长 60 秒").font(.caption).foregroundStyle(.tertiary)
            }.controlSize(.large)
        }
    }
}

private struct SpeechWorkspace: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var dictation: DictationController
    @ViewState private var options: Bool = false
    private let voices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
    private var canGenerate: Bool {
        backend.ready && !backend.busy && !audio.cannotStart && !dictation.blocksUpdate
        && !audio.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        && audio.text.unicodeScalars.count <= 500 && audio.instructions.unicodeScalars.count <= 200
    }

    var body: some View {
        VStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("朗读文本", systemImage: "text.alignleft").font(.callout.weight(.semibold))
                    Spacer()
                    Text("\(audio.text.unicodeScalars.count) / 500")
                        .font(.caption.monospacedDigit()).foregroundStyle(audio.text.unicodeScalars.count > 500 ? .red : .secondary)
                }
                PlainTextEditor(text: $audio.text, editable: !audio.generating, label: "朗读文本")
            }.padding(14).contentSurface(editor: true).frame(maxHeight: .infinity)

            HStack(spacing: 10) {
                Picker("音色", selection: $audio.voice) {
                    ForEach(voices, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ").capitalized).tag($0) }
                }.frame(width: 158)
                Picker("语言", selection: $audio.ttsLanguage) {
                    Text("自动").tag("auto"); Text("中文").tag("chinese"); Text("English").tag("english")
                }.frame(width: 128)
                Spacer(minLength: 0)
                Button { options.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                    .studioButton().help("表达指令与播放选项").accessibilityLabel("表达指令与播放选项")
                    .popover(isPresented: $options) { speechOptions }
            }.disabled(audio.generating).frame(height: 26)

            HStack(spacing: 10) {
                if audio.generating {
                    Button("取消生成", systemImage: "stop.fill") { audio.cancelGeneration() }.studioButton(prominent: true)
                } else {
                    Button { audio.generate(using: backend) } label: {
                        Label("生成并播放", systemImage: "play.fill").frame(minWidth: 120)
                    }.studioButton(prominent: true).disabled(!canGenerate).keyboardShortcut(.return, modifiers: .command)
                }
                if audio.playing {
                    Button("停止播放", systemImage: "stop.fill") { audio.stopPlayback() }.studioButton()
                } else {
                    Button("重播", systemImage: "arrow.counterclockwise") {
                        do { try audio.playGenerated() } catch { audio.reportError(error, for: .speech) }
                    }.studioButton().disabled(audio.generated.isEmpty || audio.cannotStart || dictation.blocksUpdate)
                }
                Spacer(minLength: 0)
                Button { audio.save() } label: { Image(systemName: "square.and.arrow.up") }
                    .studioButton().help("保存 WAV…").accessibilityLabel("保存 WAV")
                    .disabled(audio.generated.isEmpty || audio.generating)
            }.controlSize(.large)
        }
    }

    private var speechOptions: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("语音选项").font(.headline)
            Text("表达指令").font(.callout.weight(.medium))
            PlainTextEditor(text: $audio.instructions, label: "表达指令").frame(height: 100).contentSurface(editor: true)
            HStack {
                Text("描述语气、节奏或情绪。")
                Spacer()
                Text("\(audio.instructions.unicodeScalars.count) / 200").monospacedDigit()
            }.font(.caption).foregroundStyle(audio.instructions.unicodeScalars.count > 200 ? .red : .secondary)
            Toggle("流式播放", isOn: $audio.streaming)
            Text("生成过程中即可开始播放。").font(.caption).foregroundStyle(.secondary)
        }.padding(20).frame(width: 350)
    }
}

private struct WorkspaceStatus: View {
    let mode: WorkspaceMode
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var audio: AudioWorkspace
    @EnvironmentObject private var updater: UpdaterController
    @EnvironmentObject private var dictation: DictationController
    @ViewState private var details: Bool = false

    private var error: String? {
        if let message = updater.gate.failure ?? updater.errorMessage { return message }
        if let message = audio.errorMessage,
           (mode == .transcription && audio.errorOperation == .transcription || mode == .speech && audio.errorOperation == .speech) { return message }
        if backend.status == "failed" { return backend.detail }
        return nil
    }
    private var message: String {
        if updater.gate.waiting { return updater.displayStatus }
        if let error { return error }
        if !backend.ready { return backend.detail }
        if dictation.blocksUpdate { return dictation.statusText }
        if audio.requestingMicrophone { return "等待麦克风授权…" }
        if audio.recording { return audio.recordingSeconds >= 59 ? "已达录音上限，请停止并识别" : "正在聆听，说完后停止并识别" }
        if audio.transcribing { return "正在本机识别语音…" }
        if audio.generating { return audio.playing ? "正在生成并播放…" : "正在生成语音…" }
        if audio.playing { return "正在播放语音" }
        let timing = mode == .transcription ? audio.sttTiming : audio.ttsTiming
        return timing.isEmpty ? "本地处理 · 录音临时使用" : timing
    }

    var body: some View {
        HStack(spacing: 8) {
            if audio.transcribing || audio.generating || audio.requestingMicrophone || updater.gate.waiting {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: error == nil ? "waveform" : "exclamationmark.circle")
                    .foregroundStyle(error == nil ? StudioStyle.accent : .orange)
            }
            Text(message).font(.caption).foregroundStyle(error == nil ? .secondary : .primary).lineLimit(1)
            Spacer(minLength: 0)
            if updater.gate.waiting {
                Button("取消等待") { updater.gate.cancel() }.buttonStyle(.borderless)
            } else if error != nil || !backend.ready {
                Button("详情") { details = true }.buttonStyle(.borderless)
                    .popover(isPresented: $details) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(error == nil ? "引擎状态" : "暂时无法完成").font(.headline)
                            PlainTextEditor(text: .constant(message), editable: false, label: "状态详情").frame(height: 180)
                            SettingsLink { Text("打开设置") }
                        }.padding(20).frame(width: 350)
                    }
            } else if mode == .transcription && !dictation.available {
                SettingsLink { Text("启用快捷听写") }.buttonStyle(.borderless)
            } else if updater.availableVersion != nil {
                Button("更新可用") { updater.checkForUpdates() }.buttonStyle(.borderless).disabled(!updater.canPerformAction)
            }
        }
        .frame(height: 28)
        .padding(.horizontal, 3)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("workspace-status")
    }
}
