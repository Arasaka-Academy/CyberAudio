"""Link the model-preservation probe to the current real SDK object files."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

base = Path(__file__).resolve().parent
project = base.parents[2]
stage = sys.argv[1]
assert stage in {"baseline", "fixed"}
root = base / (stage + "-build")
root.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
native = project / ".context/icon-native-build/arm64-apple-macosx/release"
description = json.loads((native / "description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
protected = {str(p): sha(p) for p in [source, *map(Path, command["objects"]), native / "CyberAudioService"]}
prior = json.loads((project / ".context/robustness/m4-acceptance-20261106/seeded-tts-build/build.json").read_text())
compile_command = [arg.replace("SeededTTSProbe", "ModelPreservationProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "ModelPreservationProbe.o")
compile_command[-1] = str(base / "ModelPreservationProbe.swift")
with (root / "compile.log").open("xb") as log:
    subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
old_objects = project / ".context/robustness/m4-acceptance-20261106/seeded-tts-build/Objects.LinkFileList"
filelist = root / "Objects.LinkFileList"
objects = old_objects.read_text()
old_main = str(old_objects.parent / "SeededTTSProbe.o")
assert objects.count(old_main) == 1
filelist.write_text(objects.replace(old_main, str(root / "ModelPreservationProbe.o")))
link_command = [arg.replace("SeededTTSProbe", "ModelPreservationProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "model-preservation-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
with (root / "link.log").open("xb") as log:
    subprocess.run(link_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
assert protected == {name: sha(name) for name in protected}
receipt = {"stage": stage, "status": "built", "source_sha256": sha(base / "ModelPreservationProbe.swift"),
           "executable_sha256": sha(root / "model-preservation-probe"), "sdk_sha256": sha(source),
           "protected_build_inputs": protected, "compile_command": compile_command, "link_command": link_command}
(root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({key: receipt[key] for key in ["stage", "status", "sdk_sha256"]}))
