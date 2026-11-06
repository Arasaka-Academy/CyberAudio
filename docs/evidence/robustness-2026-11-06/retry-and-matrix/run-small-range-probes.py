"""Test whether a 1MiB range completes on the same degraded public route."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parent
prior = json.loads((root / "network-range-probes-20261106/result.json").read_text())
assert prior["finished"] and len(prior["cases"]) == 3
assert hashlib.sha256((root / "NetworkRangeProbe.swift").read_bytes()).hexdigest() == prior["source_sha256"]["NetworkRangeProbe.swift"]
folder = root / "network-small-range-probes-20261106"
assert not folder.exists()
folder.mkdir()
source = "https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/resolve/41d3337e8b7f2843a75841595fc14e4b9a7a4b96/model.safetensors?download=true"
result = {"started":datetime.now(timezone.utc).isoformat(), "scope":"Bounded 1MiB-range diagnostic following incomplete 8MiB probes; no production/proxy/model-partial changes", "requested_range":"117440512-118489087", "cases":[]}
target = folder / "curl-http1.bin"
process = subprocess.run(["/usr/bin/curl", "--silent", "--show-error", "--location", "--fail", "--http1.1", "--max-time", "45", "--connect-timeout", "10",
    "--proxy", "http://127.0.0.1:7890", "--range", "117440512-118489087", "--header", "Accept-Encoding: identity",
    "--output",str(target),"--dump-header",str(folder / "headers.private"),"--write-out","%{http_code} %{size_download} %{time_total} %{http_version}",source],capture_output=True,text=True,timeout=50)
(folder / "stderr.private").write_text(process.stderr)
headers = (folder / "headers.private").read_text()
row = {"transport":"curl-http1", "exit_code":process.returncode, "curl_metrics":process.stdout.strip(),
       "response_headers":[line for line in headers.splitlines() if line.lower().startswith(("http/", "content-range:", "content-length:", "content-type:"))],
       "bytes":target.stat().st_size if target.exists() else 0, "sha256":hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None}
result["cases"].append(row)
print(json.dumps(row),flush=True)
swift = folder / "SmallNetworkRangeProbe.swift"
swift.write_text((root / "NetworkRangeProbe.swift").read_text().replace("117440512-125829119","117440512-118489087"))
subprocess.run(["/usr/bin/swiftc","-parse-as-library","-target","arm64-apple-macos26.0",str(swift),"-o",str(folder / "probe")],check=True,capture_output=True,text=True,timeout=120)
process = subprocess.run([str(folder / "probe")],capture_output=True,text=True,timeout=60)
assert process.returncode == 0
row = json.loads(process.stdout)
result["cases"].append(row)
print(json.dumps(row),flush=True)
result["finished"] = datetime.now(timezone.utc).isoformat()
result["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
result["swift_source_sha256"] = hashlib.sha256(swift.read_bytes()).hexdigest()
(folder / "result.json").write_text(json.dumps(result,indent=2)+"\n")
