"""Link the routing regression to actual newly built production SDK objects."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "language-routing-green-build"
root.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
check = json.loads((base / "language-routing-check.json").read_text())
assert check["exit_code"] == 0
description = json.loads((project / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
protected_paths = [source, Path(command["objects"][command["sources"].index(str(source))]),
                   project / ".context/icon-native-build/arm64-apple-macosx/release/CyberAudioService",
                   project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService"]
protected = {str(path): sha(path) for path in protected_paths}
assert sha(source) != "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426"
prior = json.loads((base / "seeded-tts-build/build.json").read_text())
compile_command = [arg.replace("SeededTTSProbe", "LanguageRoutingProbe") for arg in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(root / "LanguageRoutingProbe.o")
compile_command[-1] = str(base / "LanguageRoutingProbe.swift")
with (root / "compile.log").open("xb") as log:
    subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
old_objects = base / "seeded-tts-build/Objects.LinkFileList"
filelist = root / "Objects.LinkFileList"
objects = old_objects.read_text()
old_main = str(base / "seeded-tts-build/SeededTTSProbe.o")
assert objects.count(old_main) == 1
filelist.write_text(objects.replace(old_main, str(root / "LanguageRoutingProbe.o")))
link_command = [arg.replace("SeededTTSProbe", "LanguageRoutingProbe") for arg in prior["link_command"]]
link_command[link_command.index("-o") + 1] = str(root / "language-routing-probe")
link_command[link_command.index("@" + str(old_objects))] = "@" + str(filelist)
with (root / "link.log").open("xb") as log:
    subprocess.run(link_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
assert protected == {name: sha(Path(name)) for name in protected}
receipt = {"status": "built", "source_sha256": sha(base / "LanguageRoutingProbe.swift"),
           "executable_sha256": sha(root / "language-routing-probe"), "plan_sha256": sha(base / "language-routing-plan.json"),
           "production_sdk_source_sha256": sha(source), "preserved_input_sha256": protected,
           "preliminary_report_sha256": sha(base / "m1-subtalker-probe/report.json"),
           "production_sampler_settings_changed": False, "compile_command": compile_command, "link_command": link_command}
(root / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print("Routing regression built against actual production managed SDK")
