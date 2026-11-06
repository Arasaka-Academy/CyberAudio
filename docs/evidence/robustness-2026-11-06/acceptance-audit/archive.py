"""Archive reviewed text only; keep full logs, configuration and credentials private."""
from pathlib import Path
import hashlib
import json
import re

base = Path(__file__).resolve().parent
project = base.parents[2]
destination = project / "docs/evidence/robustness-2026-11-06/acceptance-audit"
assert json.loads((base / "verification.json").read_text())["status"] == "verified_acceptance_incomplete"
files = [
    "refresh-m4.py", "remote-range-refresh.py", "m4-refresh.json",
    "desktop-capabilities.swift", "desktop-capabilities.json", "github-ssh-observation.json",
    "signing-inventory.py", "signing-inventory.json", "source-access.py", "source-access.json",
    "verify.py", "verification.json", "archive.py",
]
tokens = list((project / ".context/robustness").rglob("config/api-token"))
tokens.append(Path.home() / "Library/Application Support/CyberAudio/config/api-token")
secrets = [path.read_bytes().strip() for path in tokens if path.is_file()]
assert secrets and all(len(value) >= 20 for value in secrets)
for name in files:
    source, target = base / name, destination / name
    assert source.is_file() and source.stat().st_size < 1_000_000 and not target.exists()
    data = source.read_bytes()
    value = data.decode("utf-8")
    assert not any(secret in data for secret in secrets), "Secret detected; stop archival"
    assert not re.search(r"(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", value)
    target.write_bytes(data)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {path.name: sha(path) for path in sorted(destination.iterdir()) if path.is_file()}
assert len(manifest) == len(files) + 1 and "README.md" in manifest
target = destination / "archive-sha256.json"
assert not target.exists()
target.write_text(json.dumps(manifest, indent=2) + "\n")
assert all(sha(destination / name) == digest for name, digest in manifest.items())
print(json.dumps({"text_payloads": len(manifest), "bytes": sum((destination / name).stat().st_size for name in manifest), "credentials_or_media_archived": False}))
