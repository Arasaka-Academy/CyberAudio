"""Link a diagnostic executable against the existing private SDK module."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
prior_path = base / "traced-tts-build/executable-build.json"
prior = json.loads(prior_path.read_text())
root = base / "tts-matrix-build-r2"
root.mkdir(exist_ok=True)
assert not (root / "build.json").exists()
source = base / "TTSMatrixProbe.swift"
private_module = base / "traced-tts-build/Modules/MLXAudioTTS.swiftmodule"
helper = project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService"
protected = {str(path): sha(path) for path in [private_module, helper]}
assert sha(helper) == "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89"
compile_command = [arg.replace("TracedTTSProbe", "TTSMatrixProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "TTSMatrixProbe.o")
compile_command[-1] = str(source)
with (root / "compile.log").open("a") as log:
    subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
old_objects = base / "traced-tts-build/Objects.LinkFileList"
filelist = root / "Objects.LinkFileList"
objects = old_objects.read_text()
old_object = str(base / "traced-tts-build/TracedTTSProbe.o")
assert objects.count(old_object) == 1
filelist.write_text(objects.replace(old_object, str(root / "TTSMatrixProbe.o")))
link_command = [arg.replace("TracedTTSProbe", "TTSMatrixProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "tts-matrix-probe")
index = link_command.index("@" + str(old_objects))
link_command[index] = "@" + str(filelist)
with (root / "link.log").open("a") as log:
    subprocess.run(link_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
assert protected == {name: sha(Path(name)) for name in protected}
receipt = {
    "status": "built",
    "source_sha256": sha(source),
    "executable_sha256": sha(root / "tts-matrix-probe"),
    "plan_sha256": sha(base / "tts-matrix-plan.json"),
    "prior_build_sha256": sha(prior_path),
    "preserved_input_sha256": protected,
    "compile_command": compile_command,
    "link_command": link_command,
}
(root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print("Private matrix executable built; existing SDK module and production Helper preserved")
