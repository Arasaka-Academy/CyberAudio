"""Compile a private traced SDK copy; never alter the production build scratch."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base/"traced-tts-build"
root.mkdir(exist_ok=False)
(root/"Modules").mkdir()
objects = root/"MLXAudioTTS.build"
objects.mkdir()
description = json.loads((project/".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
original = source.read_text()
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(source) == "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426"
before = "        checkCancellation: (() throws -> Void)? = nil\n    ) throws -> MLXArray"
assert original.count(before) == 1
changed = original.replace(before, "        checkCancellation: (() throws -> Void)? = nil,\n        onSample: ((MLXArray, MLXArray) -> Void)? = nil\n    ) throws -> MLXArray")
begin = changed.index("    func generateVoiceDesign(")
end = changed.index("    static func parseCustomVoicePrompt", begin)
part = changed[begin:end]
before = "                eosTokenId: eosTokenId,\n                minP: minP\n"
assert part.count(before) == 1
part = part.replace(before, "                eosTokenId: eosTokenId,\n                minP: minP,\n                onSample: onSample\n")
changed = changed[:begin]+part+changed[end:]
before = "        eosTokenId: Int? = nil,\n        minP: Float = 0.0\n"
assert changed.count(before) == 1
changed = changed.replace(before, "        eosTokenId: Int? = nil,\n        minP: Float = 0.0,\n        onSample: ((MLXArray, MLXArray) -> Void)? = nil\n")
before = "        // Sample with temperature\n        let token = categorical(filteredLogits / temperature)"
assert changed.count(before) == 1
changed = changed.replace(before, "        onSample?(logitsSlice, filteredLogits)\n"+before)
changed += '''
extension Qwen3TTSModel {
    public func diagnosticManaged(
        text: String, voice: String, language: String, temperature: Float,
        checkCancellation: @escaping () throws -> Void,
        onToken: @escaping (Int) -> Void,
        onSample: @escaping (MLXArray, MLXArray) -> Void,
        onAudioChunk: @escaping (MaterializedArray) -> Void
    ) throws {
        try requireGenerationComponents()
        _ = try generateVoiceDesign(text: text, instruct: voice, language: language,
            refAudio: nil, refText: nil, temperature: temperature, topK: 50, topP: 1,
            repetitionPenalty: 1.05, minP: 0, maxTokens: 1200,
            streamingInterval: 0.32, onToken: onToken, onAudioChunk: onAudioChunk,
            checkCancellation: checkCancellation, onSample: onSample)
    }
}
'''
private = root/"Qwen3TTS.swift"
private.write_text(changed)
old_map = json.loads(Path(command["outputFileMapPath"]).read_text())
new_map = {}
for name, values in old_map.items():
    key = str(private) if name == str(source) else name
    new_map[key] = {kind: str(objects/Path(path).name) for kind, path in values.items()}
filemap = root/"output-file-map.json"
filemap.write_text(json.dumps(new_map, indent=2)+"\n")
sources = [str(private) if p == str(source) else p for p in command["sources"]]
arguments = [x for x in command["otherArguments"] if x != "-parseable-output"]
cmd = [command["executable"], "-module-name", "MLXAudioTTS", "-emit-dependencies", "-emit-module",
       "-emit-module-path", str(root/"Modules/MLXAudioTTS.swiftmodule"),
       "-output-file-map", str(filemap), "-I", command["importPath"], *arguments, "-c", *sources]
subprocess.run(cmd, check=True, timeout=600)
assert sha(source) == hashlib.sha256(original.encode()).hexdigest()
receipt = {"status": "compiled", "purpose": "Private EOS sampler tracing and temperature hypothesis only",
           "production_source_modified": False, "production_runtime_modified": False,
           "original_sdk_sha256": sha(source), "diagnostic_sdk_sha256": sha(private),
           "original_sdk_object_sha256": sha(Path(command["objects"][command["sources"].index(str(source))])),
           "compile_command": cmd, "object_mapping": {
               str(old): str(objects/Path(old).name) for old in command["objects"]}}
assert all(Path(p).is_file() for p in receipt["object_mapping"].values())
(root/"build.json").write_text(json.dumps(receipt, indent=2)+"\n")
print("Private traced MLXAudioTTS compiled; production source and objects preserved")
