"""Bounded public metadata probes; no model, audio, proxy or TLS changes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import urllib.error
import urllib.request

base = Path(__file__).resolve().parent
endpoints = [
    ("hf_dataset_card", "https://hf.co/datasets/CAiRE/ASCEND/raw/main/README.md"),
    ("github_repository_metadata", "https://api.github.com/repos/CAiRE/ASCEND"),
]


def probe(item):
    name, url = item
    row = {"name": name, "url": url, "started": datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "CyberAudio-acceptance-audit/1.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read(131073)
            row.update(http_status=response.status, bytes_read=len(body), capped=len(body) > 131072)
            if len(body) <= 131072:
                target = base / (name + ".response.private")
                target.write_bytes(body)
                row["response_sha256"] = hashlib.sha256(body).hexdigest()
    except urllib.error.HTTPError as error:
        row.update(http_status=error.code, error_type="HTTPError")
    except urllib.error.URLError as error:
        reason = error.reason
        row.update(error_type="URLError", reason_type=type(reason).__name__)
        if isinstance(reason, OSError):
            row["errno"] = reason.errno
        if "timed out" in str(reason).lower():
            row["timeout"] = True
    except TimeoutError:
        row.update(error_type="TimeoutError", timeout=True)
    row["finished"] = datetime.now(timezone.utc).isoformat()
    return row


with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(probe, endpoints))
report = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "scope": "Two bounded alternate public metadata routes; no audio or model loads",
    "results": results,
    "tls_bypassed": False,
    "proxy_configuration_changed": False,
    "natural_code_switch_acceptance": False,
    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
(base / "source-access.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
