"""Compile a private SDK input-layout diagnostic without replacing shared build objects."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "text-layout-build"
root.mkdir(exist_ok=False)
(root / "Modules").mkdir()
objects = root / "MLXAudioTTS.build"
objects.mkdir()
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
description = json.loads((project / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
assert sha(source) == "0190fd2a02d6ce0eaa3732ca7b7d373be251747edc5d14a0d0b4fe8ce3c0dd6b"
protected_paths = [source, Path(command["objects"][command["sources"].index(str(source))]),
                   project / ".context/icon-native-build/arm64-apple-macosx/release/CyberAudioService",
                   project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService",
                   base / "final-bytes-language-routing/CyberAudio.app/Contents/Helpers/CyberAudioService"]
protected = {str(path): sha(path) for path in protected_paths}
changed = source.read_text()


def replace(before, after):
    global changed
    assert changed.count(before) == 1, before
    changed = changed.replace(before, after)


replace("        checkCancellation: (() throws -> Void)? = nil\n    ) throws -> MLXArray",
        "        checkCancellation: (() throws -> Void)? = nil,\n        sequentialText: Bool = false\n    ) throws -> MLXArray")
replace("                speaker: speaker,\n                refAudio: refAudio\n            )",
        "                speaker: speaker,\n                refAudio: refAudio,\n                sequentialText: sequentialText\n            )")
replace("        refAudio: MLXArray? = nil\n    ) -> (MLXArray, MLXArray, MLXArray)",
        "        refAudio: MLXArray? = nil,\n        sequentialText: Bool = false\n    ) -> (MLXArray, MLXArray, MLXArray)")
before = "        // Pair all text and EOS with codec_pad, then start audio with tts_pad + codec_bos."
replace(before, '''        if sequentialText {
            // Reference non_streaming_mode=False: first text token with codec_bos,
            // remaining text and tts_eos consumed one step at a time.
            let first = textEmbed[0..., 3 ..< 4, 0...] + codecEmbedSuffix[0..., 1 ..< 2, 0...]
            inputEmbeds = concatenated([inputEmbeds, first], axis: 1)
            let trailing = concatenated([textEmbed[0..., 4 ..< (textEmbed.dim(1) - 5), 0...], ttsEosEmbed], axis: 1)
            return (inputEmbeds, trailing, ttsPadEmbed)
        }

''' + before)
changed += '''
extension Qwen3TTSModel {
    public func diagnosticTextLayoutManaged(
        text: String, voice: String, language: String, sequentialText: Bool,
        checkCancellation: @escaping () throws -> Void,
        onToken: @escaping (Int) -> Void,
        onAudioChunk: @escaping (MaterializedArray) -> Void
    ) throws {
        try requireGenerationComponents()
        _ = try generateVoiceDesign(text: text, instruct: voice, language: language,
            refAudio: nil, refText: nil, temperature: 0.7, topK: 50, topP: 1,
            repetitionPenalty: 1.05, minP: 0, maxTokens: 1200,
            streamingInterval: 0.32, onToken: onToken, onAudioChunk: onAudioChunk,
            checkCancellation: checkCancellation, sequentialText: sequentialText)
    }
}
'''
private = root / "Qwen3TTS.swift"
private.write_text(changed)
old_map = json.loads(Path(command["outputFileMapPath"]).read_text())
new_map = {str(private) if name == str(source) else name:
           {kind: str(objects / Path(path).name) for kind, path in values.items()}
           for name, values in old_map.items()}
filemap = root / "output-file-map.json"
filemap.write_text(json.dumps(new_map, indent=2) + "\n")
sources = [str(private) if p == str(source) else p for p in command["sources"]]
arguments = [x for x in command["otherArguments"] if x != "-parseable-output"]
module_command = [command["executable"], "-module-name", "MLXAudioTTS", "-emit-dependencies", "-emit-module",
                  "-emit-module-path", str(root / "Modules/MLXAudioTTS.swiftmodule"),
                  "-output-file-map", str(filemap), "-I", command["importPath"], *arguments, "-c", *sources]
prior = json.loads((base / "seeded-tts-build/build.json").read_text())
compile_command = [arg.replace("SeededTTSProbe", "TextLayoutProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "TextLayoutProbe.o")
compile_command[-1] = str(base / "TextLayoutProbe.swift")
compile_command[1:1] = ["-I", str(root / "Modules")]
old_objects = base / "seeded-tts-build/Objects.LinkFileList"
object_lines = old_objects.read_text().splitlines()
mapping = {str(old): str(objects / Path(old).name) for old in command["objects"]}
old_main = str(base / "seeded-tts-build/SeededTTSProbe.o")
assert object_lines.count(old_main) == 1
filelist = root / "Objects.LinkFileList"
filelist.write_text("\n".join(str(root / "TextLayoutProbe.o") if line == old_main else mapping.get(line, line)
                             for line in object_lines) + "\n")
link_command = [arg.replace("SeededTTSProbe", "TextLayoutProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "text-layout-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
began = time.monotonic()
receipt = {"status": "building", "scope": "Private SDK sequential-text input diagnostic; no sampling/default/runtime changes",
           "source_sha256": sha(base / "TextLayoutProbe.swift"), "plan_sha256": sha(base / "text-layout-plan.json"),
           "diagnostic_sdk_sha256": sha(private), "preserved_input_sha256": protected}
try:
    for name, args, timeout in [("module", module_command, 600), ("probe", compile_command, 180), ("link", link_command, 180)]:
        if name == "link":
            assert all(Path(shlex.split(p)[0]).is_file() for p in filelist.read_text().splitlines())
        with (root / (name + ".log")).open("xb") as log:
            subprocess.run(args, check=True, timeout=timeout, stdout=log, stderr=subprocess.STDOUT)
    receipt["status"] = "built"
    receipt["executable_sha256"] = sha(root / "text-layout-probe")
    receipt["module_compile_command"] = module_command
    receipt["compile_command"] = compile_command
    receipt["link_command"] = link_command
except Exception as error:
    receipt["status"] = "failed"; receipt["error"] = str(error)
    raise
finally:
    receipt["elapsed_seconds"] = time.monotonic() - began
    receipt["shared_and_production_inputs_preserved"] = protected == {name: sha(Path(name)) for name in protected}
    (root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
assert receipt["shared_and_production_inputs_preserved"]
print("Private text-layout probe built; formal SDK, objects and Helpers unchanged")
