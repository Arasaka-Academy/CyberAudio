"""Compile only into a new private tree; keep formal SDK/App/Helper bytes intact."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess
import sys

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "subtalker-build"
resume_link = sys.argv[1:] == ["--resume-link"]
assert not sys.argv[1:] or resume_link
root.mkdir(exist_ok=resume_link)
(root / "Modules").mkdir(exist_ok=resume_link)
objects = root / "MLXAudioTTS.build"
objects.mkdir(exist_ok=resume_link)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
description = json.loads((project / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
original = source.read_text()
assert sha(source) == "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426"
protected_paths = [source, Path(command["objects"][command["sources"].index(str(source))]),
                   project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService",
                   base / "final-bytes-small-range/CyberAudio.app/Contents/Helpers/CyberAudioService"]
protected = {str(path): sha(path) for path in protected_paths}
signature = "        checkCancellation: (() throws -> Void)? = nil\n    ) throws -> MLXArray"
assert original.count(signature) == 1
changed = original.replace(signature, "        checkCancellation: (() throws -> Void)? = nil,\n        subtalkerTemperature: Float? = nil\n    ) throws -> MLXArray")
before = "                let nextCode = sampleToken(\n                    codeLogits,\n                    temperature: temperature,"
assert changed.count(before) == 1
changed = changed.replace(before, before.replace("temperature: temperature,", "temperature: subtalkerTemperature ?? temperature,"))
changed += '''
extension Qwen3TTSModel {
    public func diagnosticSubtalkerManaged(
        text: String, voice: String, language: String, subtalkerTemperature: Float,
        checkCancellation: @escaping () throws -> Void,
        onToken: @escaping (Int) -> Void,
        onAudioChunk: @escaping (MaterializedArray) -> Void
    ) throws {
        try requireGenerationComponents()
        _ = try generateVoiceDesign(text: text, instruct: voice, language: language,
            refAudio: nil, refText: nil, temperature: 0.7, topK: 50, topP: 1,
            repetitionPenalty: 1.05, minP: 0, maxTokens: 1200,
            streamingInterval: 0.32, onToken: onToken, onAudioChunk: onAudioChunk,
            checkCancellation: checkCancellation, subtalkerTemperature: subtalkerTemperature)
    }
}
'''
private = root / "Qwen3TTS.swift"
if resume_link:
    assert private.read_text() == changed
    assert (root / "Modules/MLXAudioTTS.swiftmodule").is_file()
    assert all((objects / Path(path).name).is_file() for path in command["objects"])
else:
    private.write_text(changed)
old_map = json.loads(Path(command["outputFileMapPath"]).read_text())
new_map = {}
for name, values in old_map.items():
    key = str(private) if name == str(source) else name
    new_map[key] = {kind: str(objects / Path(path).name) for kind, path in values.items()}
filemap = root / "output-file-map.json"
filemap.write_text(json.dumps(new_map, indent=2) + "\n")
sources = [str(private) if p == str(source) else p for p in command["sources"]]
arguments = [x for x in command["otherArguments"] if x != "-parseable-output"]
module_command = [command["executable"], "-module-name", "MLXAudioTTS", "-emit-dependencies", "-emit-module",
                  "-emit-module-path", str(root / "Modules/MLXAudioTTS.swiftmodule"),
                  "-output-file-map", str(filemap), "-I", command["importPath"], *arguments, "-c", *sources]
if not resume_link:
    with (root / "module-compile.log").open("xb") as log:
        subprocess.run(module_command, check=True, timeout=600, stdout=log, stderr=subprocess.STDOUT)
prior = json.loads((base / "seeded-tts-build/build.json").read_text())
compile_command = [arg.replace("SeededTTSProbe", "SubtalkerProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "SubtalkerProbe.o")
compile_command[-1] = str(base / "SubtalkerProbe.swift")
compile_command[1:1] = ["-I", str(root / "Modules")]
with (root / "probe-compile.log").open("ab" if resume_link else "xb") as log:
    subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
old_objects = base / "seeded-tts-build/Objects.LinkFileList"
object_lines = old_objects.read_text().splitlines()
mapping = {str(old): str(objects / Path(old).name) for old in command["objects"]}
old_main = str(base / "seeded-tts-build/SeededTTSProbe.o")
assert object_lines.count(old_main) == 1
filelist = root / "Objects.LinkFileList"
filelist.write_text("\n".join(str(root / "SubtalkerProbe.o") if line == old_main else mapping.get(line, line)
                             for line in object_lines) + "\n")
assert all(Path(shlex.split(p)[0]).is_file() for p in filelist.read_text().splitlines())
link_command = [arg.replace("SeededTTSProbe", "SubtalkerProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "subtalker-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
with (root / "link.log").open("xb") as log:
    subprocess.run(link_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
assert protected == {name: sha(Path(name)) for name in protected}
receipt = {"status": "built", "purpose": "Only independent subtalker-temperature diagnostic; no production change",
           "production_source_modified": False, "production_runtime_modified": False,
           "diagnostic_sdk_sha256": sha(private), "source_sha256": sha(base / "SubtalkerProbe.swift"),
           "executable_sha256": sha(root / "subtalker-probe"), "plan_sha256": sha(base / "subtalker-plan.json"),
           "resumed_link_after_quoted_path_validation_fix": resume_link,
           "preserved_input_sha256": protected, "module_compile_command": module_command,
           "compile_command": compile_command, "link_command": link_command}
(root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print("Private subtalker probe compiled; production SDK/App/Helpers preserved")
