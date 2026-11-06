"""Compile private read-only model probes without replacing shared SDK objects."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "decoder-adapter-build"
root.mkdir(exist_ok=False)
(root / "Modules").mkdir()
objects = root / "MLXAudioTTS.build"
objects.mkdir()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
description = json.loads((project / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
assert sha(source) == "17301a083f323673bc548aff031b83d9df60b2c04715af87592b1c68d42bd48a"
protected_paths = [source, *map(Path, command["objects"]),
    project / ".context/icon-native-build/arm64-apple-macosx/release/CyberAudioService",
    project / ".context/icon-native-build/arm64-apple-macosx/release/CyberAudioAssets",
    project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService",
    project / ".context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app/Contents/Helpers/CyberAudioService"]
protected = {str(path): sha(path) for path in protected_paths}
changed = source.read_text() + (base / "decoder-adapter.swift").read_text()
private = root / "Qwen3TTS.swift"
private.write_text(changed)
old_map = json.loads(Path(command["outputFileMapPath"]).read_text())
new_map = {str(private) if name == str(source) else name:
    {kind: str(objects / Path(path).name) for kind, path in values.items()} for name, values in old_map.items()}
filemap = root / "output-file-map.json"
filemap.write_text(json.dumps(new_map, indent=2) + "\n")
sources = [str(private) if p == str(source) else p for p in command["sources"]]
arguments = [x for x in command["otherArguments"] if x != "-parseable-output"]
(objects / "include").mkdir()
arguments[arguments.index("-emit-objc-header-path") + 1] = str(objects / "include/MLXAudioTTS-Swift.h")
arguments[arguments.index("-module-cache-path") + 1] = str(root / "ModuleCache")
module_command = [command["executable"], "-module-name", "MLXAudioTTS", "-emit-dependencies", "-emit-module",
    "-emit-module-path", str(root / "Modules/MLXAudioTTS.swiftmodule"), "-output-file-map", str(filemap),
    "-I", command["importPath"], *arguments, "-c", *sources]
prior_root = project / ".context/robustness/m4-acceptance-20261106/seeded-tts-build"
prior = json.loads((prior_root / "build.json").read_text())
compile_command = [arg.replace("SeededTTSProbe", "ReferenceDecodeAdapterProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "ReferenceDecodeAdapterProbe.o")
compile_command[-1] = str(base / "ReferenceDecodeAdapterProbe.swift")
compile_command[compile_command.index("-module-cache-path") + 1] = str(root / "ModuleCache")
compile_command[1:1] = ["-I", str(root / "Modules")]
old_objects = prior_root / "Objects.LinkFileList"
object_lines = old_objects.read_text().splitlines()
mapping = {str(old): str(objects / Path(old).name) for old in command["objects"]}
old_main = str(prior_root / "SeededTTSProbe.o")
assert object_lines.count(old_main) == 1
filelist = root / "Objects.LinkFileList"
filelist.write_text("\n".join(str(root / "ReferenceDecodeAdapterProbe.o") if line == old_main else mapping.get(line, line)
    for line in object_lines) + "\n")
link_command = [arg.replace("SeededTTSProbe", "ReferenceDecodeAdapterProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "reference-decode-adapter-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
start = time.monotonic()
receipt = {"status": "building", "purpose": "Private same-code decoder access adapter; no algorithm changes",
    "plan_sha256": sha(base / "decoder-adapter-plan.json"), "source_sha256": sha(base / "ReferenceDecodeAdapterProbe.swift"),
    "original_sdk_sha256": sha(source), "diagnostic_sdk_sha256": sha(private), "preserved_input_sha256": protected}
try:
    for name, args, timeout in [("module", module_command, 600), ("probe", compile_command, 180), ("link", link_command, 180)]:
        if name == "link":
            assert all(Path(shlex.split(p)[0]).is_file() for p in filelist.read_text().splitlines())
        process = None
        try:
            with (root / (name + ".log")).open("xb") as log:
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                result = process.wait(timeout=timeout)
                assert result == 0, f"Private {name} compile failed"
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
    receipt["status"] = "built"
    receipt["executable_sha256"] = sha(root / "reference-decode-adapter-probe")
    receipt["module_compile_command"] = module_command
    receipt["compile_command"] = compile_command
    receipt["link_command"] = link_command
except Exception as error:
    receipt["status"] = "failed"
    receipt["error"] = str(error)
    raise
finally:
    receipt["elapsed_seconds"] = time.monotonic() - start
    receipt["shared_and_production_inputs_preserved"] = protected == {name: sha(name) for name in protected}
    (root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
assert receipt["shared_and_production_inputs_preserved"]
print("Private decoder access module built; formal SDK/objects/Helpers unchanged")
