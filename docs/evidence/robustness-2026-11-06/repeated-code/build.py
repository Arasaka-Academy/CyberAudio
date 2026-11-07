"""Link one original and one private repeated-code diagnostic; preserve shared objects."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PLAN = json.loads((ROOT / "protocol.json").read_text())
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=False)
(BUILD / "Modules").mkdir()
objects = BUILD / "MLXAudioTTS.build"
objects.mkdir()
(objects / "include").mkdir()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


description = json.loads((PROJECT / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
source = Path(PLAN["sdk_source"])
assert sha(source) == PLAN["sdk_source_sha256"]
private = BUILD / "Qwen3TTS.swift"
text = source.read_text()
assert text.count("        // Greedy if temperature 0\n") == 1
insertion = (ROOT / "insertion.swift.txt").read_text()
private.write_text(text.replace("        // Greedy if temperature 0\n", insertion + "        // Greedy if temperature 0\n", 1))
assert private.read_text().replace(insertion, "", 1) == text
original_map = json.loads(Path(command["outputFileMapPath"]).read_text())
filemap = BUILD / "output-file-map.json"
filemap.write_text(json.dumps({str(private) if key == str(source) else key:
                              {kind: str(objects / Path(path).name) for kind, path in values.items()}
                              for key, values in original_map.items()}, indent=2) + "\n")
sources = [str(private) if path == str(source) else path for path in command["sources"]]
arguments = [value for value in command["otherArguments"] if value != "-parseable-output"]
arguments[arguments.index("-emit-objc-header-path") + 1] = str(objects / "include/MLXAudioTTS-Swift.h")
module_command = [command["executable"], "-module-name", "MLXAudioTTS", "-emit-dependencies", "-emit-module",
                  "-emit-module-path", str(BUILD / "Modules/MLXAudioTTS.swiftmodule"), "-output-file-map", str(filemap),
                  "-I", command["importPath"], *arguments, "-c", *sources]
prior_root = ROOT.parent / "m4-acceptance-20261106/seeded-tts-build"
prior = json.loads((prior_root / "build.json").read_text())
compile_command = [value.replace("SeededTTSProbe", "RepeatedCodeProbe") for value in prior["compile_command"]]
compile_command[compile_command.index("-o") + 1] = str(BUILD / "RepeatedCodeProbe.o")
compile_command[-1] = str(ROOT / "RepeatedCodeProbe.swift")
original_filelist = prior_root / "Objects.LinkFileList"
original_lines = original_filelist.read_text().splitlines()
old_main = str(prior_root / "SeededTTSProbe.o")
assert original_lines.count(old_main) == 1
mapping = {str(old): str(objects / Path(old).name) for old in command["objects"]}
baseline_list = BUILD / "Baseline.LinkFileList"
candidate_list = BUILD / "Candidate.LinkFileList"
baseline_lines = [str(BUILD / "RepeatedCodeProbe.o") if line == old_main else line for line in original_lines]
candidate_lines = [mapping.get(line, line) for line in baseline_lines]
baseline_list.write_text("\n".join(baseline_lines) + "\n")
candidate_list.write_text("\n".join(candidate_lines) + "\n")
assert sum(a != b for a, b in zip(baseline_lines, candidate_lines)) == len(command["objects"])
link = prior["link_command"]
commands = [("caller", compile_command, 180), ("private-module", module_command, 600)]
for arm, filelist in [("baseline", baseline_list), ("candidate", candidate_list)]:
    args = [value.replace("SeededTTSProbe", "RepeatedCodeProbe") for value in link]
    args[args.index("-o") + 1] = str(BUILD / (arm + "-probe"))
    args[args.index("@" + str(original_filelist))] = "@" + str(filelist)
    commands.append((arm + "-link", args, 180))
protected_paths = {source, *map(Path, command["objects"]),
                   *(Path(shlex.split(line)[0]) for line in original_lines)}
protected = {str(path): sha(path) for path in protected_paths}
record = {"status": "building", "plan_sha256": sha(ROOT / "protocol.json"), "original_sdk_sha256": sha(source),
          "private_sdk_sha256": sha(private), "protected_inputs_sha256": protected, "steps": [],
          "changed_link_objects": len(command["objects"]), "identical_caller_for_both_arms": True}
began = time.monotonic()
try:
    for name, args, timeout in commands:
        if name.endswith("-link"):
            filelist = baseline_list if name == "baseline-link" else candidate_list
            assert all(Path(shlex.split(line)[0]).is_file() for line in filelist.read_text().splitlines())
        process = None
        try:
            with (BUILD / (name + ".private.log")).open("xb") as log:
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                code = process.wait(timeout=timeout)
                record["steps"].append({"name": name, "exit_code": code})
                assert code == 0, "Private compile failed: " + name
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=12)
    record["executables_sha256"] = {arm: sha(BUILD / (arm + "-probe")) for arm in ["baseline", "candidate"]}
    record["status"] = "built_no_model"
except BaseException as error:
    record.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    record["elapsed_seconds"] = time.monotonic() - began
    record["shared_inputs_preserved"] = protected == {name: sha(name) for name in protected}
    with (BUILD / "build.json").open("x") as stream:
        json.dump(record, stream, indent=2); stream.write("\n")
assert record["status"] == "built_no_model" and record["shared_inputs_preserved"]
print(json.dumps({key: record[key] for key in ["status", "elapsed_seconds", "shared_inputs_preserved", "changed_link_objects"]}, indent=2))
