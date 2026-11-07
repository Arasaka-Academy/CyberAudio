"""Compile one new caller; link the exact already-validated private TTS objects."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


plan = json.loads((ROOT / "matrix-plan.json").read_text())
pilot = json.loads((ROOT / "build/build.json").read_text())
assert pilot["status"] == "built_no_model" and pilot["shared_inputs_preserved"]
assert pilot["protected_inputs_sha256"] == {name: sha(name) for name in pilot["protected_inputs_sha256"]}
assert plan["files_sha256"] == {name: sha(ROOT / name) for name in plan["files_sha256"]}
build = ROOT / "matrix-build"
build.mkdir(exist_ok=False)
prior = ROOT.parent / "m4-acceptance-20261106/seeded-tts-build"
commands = json.loads((prior / "build.json").read_text())
compile_args = list(commands["compile_command"])
compile_args[compile_args.index("-o") + 1] = str(build / "RepeatedCodeMatrixProbe.o")
compile_args[-1] = str(ROOT / "RepeatedCodeMatrixProbe.swift")
lines = (ROOT / "build/Candidate.LinkFileList").read_text().splitlines()
assert lines.count(str(ROOT / "build/RepeatedCodeProbe.o")) == 1
filelist = build / "Objects.LinkFileList"
filelist.write_text("\n".join(str(build / "RepeatedCodeMatrixProbe.o") if line == str(ROOT / "build/RepeatedCodeProbe.o") else line for line in lines) + "\n")
link_args = list(commands["link_command"])
link_args[link_args.index("-o") + 1] = str(build / "candidate-matrix-probe")
link_args[link_args.index("@" + str(prior / "Objects.LinkFileList"))] = "@" + str(filelist)
private_objects = sorted((ROOT / "build/MLXAudioTTS.build").glob("*.o"))
record = {"status": "building", "plan_sha256": sha(ROOT / "matrix-plan.json"),
          "private_objects_sha256": {str(p): sha(p) for p in private_objects}, "compile_command": compile_args, "link_command": link_args}
try:
    for name, args in [("compile", compile_args), ("link", link_args)]:
        with (build / (name + ".private.log")).open("xb") as log:
            subprocess.run(args, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    record.update(status="built_no_inference", executable_sha256=sha(build / "candidate-matrix-probe"))
finally:
    record["shared_inputs_preserved"] = pilot["protected_inputs_sha256"] == {name: sha(name) for name in pilot["protected_inputs_sha256"]}
    record["private_objects_preserved"] = record["private_objects_sha256"] == {name: sha(name) for name in record["private_objects_sha256"]}
    (build / "build.json").write_text(json.dumps(record, indent=2) + "\n")
assert record["shared_inputs_preserved"] and record["private_objects_preserved"]
print(json.dumps({key: record[key] for key in ["status", "shared_inputs_preserved", "private_objects_preserved"]}))
