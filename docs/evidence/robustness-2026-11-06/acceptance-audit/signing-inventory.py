"""Count signing identities without retaining names, keys or certificates."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

base = Path(__file__).resolve().parent
result = subprocess.run(
    ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
    capture_output=True, text=True, timeout=15,
)
assert result.returncode == 0
value = result.stdout + result.stderr
report = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "identity_command_exit": result.returncode,
    "apple_development_count": len(re.findall(r'"Apple Development:', value)),
    "developer_id_application_count": len(re.findall(r'"Developer ID Application:', value)),
    "orca_cli_present": shutil.which("orca") is not None,
    "orca_app_present": any(path.exists() for path in [Path("/Applications/Orca.app"), Path.home() / "Applications/Orca.app"]),
    "identity_names_or_certificates_retained": False,
    "configuration_changed": False,
    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
(base / "signing-inventory.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
