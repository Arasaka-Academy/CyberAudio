"""Link the predeclared matrix against the existing private subtalker module."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
prior_path = base / "subtalker-build/build.json"
prior = json.loads(prior_path.read_text())
root = base / "subtalker-matrix-build"
root.mkdir(exist_ok=False)
source = base / "SubtalkerMatrixProbe.swift"
module = base / "subtalker-build/Modules/MLXAudioTTS.swiftmodule"
protected = {str(module): sha(module), **prior["preserved_input_sha256"]}
assert all(sha(Path(name)) == digest for name, digest in protected.items())
compile_command = [arg.replace("SubtalkerProbe", "SubtalkerMatrixProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "SubtalkerMatrixProbe.o")
compile_command[-1] = str(source)
with (root / "compile.log").open("xb") as log:
    subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
old_objects = base / "subtalker-build/Objects.LinkFileList"
filelist = root / "Objects.LinkFileList"
objects = old_objects.read_text()
old_main = str(base / "subtalker-build/SubtalkerProbe.o")
assert objects.count(old_main) == 1
filelist.write_text(objects.replace(old_main, str(root / "SubtalkerMatrixProbe.o")))
link_command = [arg.replace("SubtalkerProbe", "SubtalkerMatrixProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "subtalker-matrix-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
with (root / "link.log").open("xb") as log:
    subprocess.run(link_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
assert protected == {name: sha(Path(name)) for name in protected}
receipt = {"status": "built", "source_sha256": sha(source), "executable_sha256": sha(root / "subtalker-matrix-probe"),
           "plan_sha256": sha(base / "subtalker-matrix-plan.json"), "prior_build_sha256": sha(prior_path),
           "preliminary_report_sha256": sha(base / "m1-subtalker-probe/report.json"),
           "preliminary_asr_sha256": sha(base / "m1-subtalker-roundtrip/reports/asr-followup.json"),
           "baseline_report_sha256": sha(base / "m1-tts-matrix-r2/report.json"),
           "baseline_asr_sha256": sha(base / "m1-tts-matrix-roundtrip/reports/asr-followup.json"),
           "preserved_input_sha256": protected, "compile_command": compile_command, "link_command": link_command}
(root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print("Private 144-candidate matrix built; original SDK/App/Helpers preserved")
