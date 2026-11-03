"""Same-host migration: APFS clone an already validated venv, then relocate entrypoints."""
import argparse
from pathlib import Path
import shlex
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--destination", type=Path, required=True)
parser.add_argument("--repair-existing", action="store_true")
args = parser.parse_args()
source = args.source.resolve()
destination = args.destination.absolute()
if destination.exists() and not args.repair_existing:
    raise RuntimeError("Destination exists; preserve or remove it explicitly first")
if not destination.exists():
    subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], check=True)
for path in [destination / "pyvenv.cfg", *list((destination / "bin").iterdir())]:
    if path.is_symlink() or not path.is_file():
        continue
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        continue
    updated = content.replace(str(source), str(destination)).replace(str(args.source), str(destination))
    if updated.startswith("#!" + str(destination / "bin/python")):
        # A kernel shebang cannot contain an unescaped interpreter path with spaces.
        first, rest = updated.split("\n", 1)
        interpreter = first[2:]
        updated = "#!/bin/sh\n'''exec' " + shlex.quote(interpreter) + " \"$0\" \"$@\"\n' '''\n" + rest
    if updated != content:
        path.write_text(updated)
result = subprocess.run([str(destination / "bin/python"), "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
(destination.parent / "requirements.installed.txt").write_text(result.stdout)
print("Independent runtime cloned and entrypoints relocated.")
