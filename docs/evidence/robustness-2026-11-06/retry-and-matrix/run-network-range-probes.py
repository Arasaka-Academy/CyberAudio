"""Run three bounded range probes after the real download has terminated."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parent
receipt = json.loads((root / "download-retry-result.json").read_text())
assert receipt["status"] == "failed" and receipt["finished"] and receipt["exit_code"] == 1
assert receipt["production_preserved"] and receipt["configuration_token_preserved"]
rows = subprocess.run(["/bin/ps","-axo","pid=,ppid=,lstart=,command="],capture_output=True,text=True,check=True).stdout.splitlines()
assert not any(("run-download-retry-candidate.py" in row or "/CyberAudioAssets download " in row) and str(root) in row for row in rows)
folder = root / "network-range-probes-20261106"
assert not folder.exists()
folder.mkdir()
source = "https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/resolve/41d3337e8b7f2843a75841595fc14e4b9a7a4b96/model.safetensors?download=true"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
result = {"started":datetime.now(timezone.utc).isoformat(),"scope":"Three predeclared same-range transports through existing explicit proxy; no model download runner restart or production changes", "requested_range":"117440512-125829119", "cases":[]}
for label, flags in [("curl-default", []),("curl-http1", ["--http1.1"])]:
    target = folder / (label + ".bin")
    command = ["/usr/bin/curl", "--silent", "--show-error", "--location", "--fail", "--max-time", "45", "--connect-timeout", "10",
               "--proxy", "http://127.0.0.1:7890", "--range", "117440512-125829119", "--header", "Accept-Encoding: identity",
               "--dump-header", str(folder / (label + "-headers.private")), "--output", str(target),
               "--write-out", "%{http_code} %{size_download} %{time_total} %{http_version}", *flags, source]
    process = subprocess.run(command,capture_output=True,text=True,timeout=50)
    (folder / (label + "-stderr.private")).write_text(process.stderr)
    headers = (folder / (label + "-headers.private")).read_text()
    safe_headers = [line for line in headers.splitlines() if line.lower().startswith(("http/", "content-range:", "content-length:", "content-type:", "x-cache:", "age:"))]
    row = {"transport":label,"exit_code":process.returncode,"curl_metrics":process.stdout.strip(),"response_headers":safe_headers,
           "bytes":target.stat().st_size if target.exists() else 0,"sha256":sha(target) if target.exists() else None}
    result["cases"].append(row)
    print(json.dumps(row),flush=True)
subprocess.run(["/usr/bin/swiftc", "-parse-as-library", "-target", "arm64-apple-macos26.0", str(root / "NetworkRangeProbe.swift"), "-o", str(folder / "network-range-probe")],check=True,timeout=120,capture_output=True,text=True)
process = subprocess.run([str(folder / "network-range-probe")],capture_output=True,text=True,timeout=60)
assert process.returncode == 0
row = json.loads(process.stdout)
result["cases"].append(row)
print(json.dumps(row),flush=True)
result["finished"] = datetime.now(timezone.utc).isoformat()
result["source_sha256"] = {name:sha(root/name) for name in ["NetworkRangeProbe.swift", "run-network-range-probes.py"]}
(folder / "result.json").write_text(json.dumps(result,indent=2)+"\n")
