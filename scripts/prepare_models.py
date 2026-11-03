"""Download pinned public models, resume ranges, verify every downloaded file."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request

REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
REVISION = "41d3337e8b7f2843a75841595fc14e4b9a7a4b96"
ASR_REPO = "mlx-community/Qwen3-ASR-1.7B-8bit"
ASR_REVISION = "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(root: Path, proxy: str, repo=REPO, revision=REVISION):
    root.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy} if proxy else {}))
    with opener.open(f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true", timeout=45) as response:
        metadata = json.load(response)
    if metadata["sha"] != revision:
        raise RuntimeError("Model revision mismatch")
    manifest = {"repository": repo, "revision": revision, "files": []}
    for item in metadata["siblings"]:
        name, size = item["rfilename"], item["size"]
        if name.startswith("."):
            continue
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = item.get("lfs", {}).get("sha256")

        def valid():
            if not destination.exists() or destination.stat().st_size != size:
                return False
            return not digest or sha256_file(destination) == digest

        url = f"https://huggingface.co/{repo}/resolve/{revision}/{urllib.parse.quote(name)}?download=true"
        if not valid():
            if size < 8 * 1024 * 1024:
                for attempt in range(5):
                    try:
                        with opener.open(url, timeout=90) as response:
                            destination.write_bytes(response.read())
                        if valid():
                            break
                    except Exception:
                        if attempt == 4:
                            raise RuntimeError(f"Asset download failed: {name}") from None
                if not valid():
                    raise RuntimeError(f"Asset validation failed: {name}")
            else:
                proxy_args = ["--proxy", proxy] if proxy else []
                resolve = subprocess.run(["/usr/bin/curl", "--http1.1", "-fsSI", "-L", *proxy_args, "--max-time", "45", "-o", "/dev/null", "-w", "%{url_effective}", url], capture_output=True, text=True, check=True)
                resolved = resolve.stdout
                parts = root / (name.replace("/", "_") + ".parts")
                parts.mkdir(exist_ok=True)
                block = 16 * 1024 * 1024

                def part(start):
                    end = min(start + block, size) - 1
                    target = parts / str(start)
                    if target.exists() and target.stat().st_size == end - start + 1:
                        return target
                    partial = parts / f"{start}.partial"
                    for attempt in range(8):
                        existing = partial.stat().st_size if partial.exists() else 0
                        if existing == end - start + 1:
                            partial.replace(target)
                            return target
                        offset = start + existing
                        headers, incoming = parts / f"{start}.headers", parts / f"{start}.incoming"
                        route = ["--noproxy", "*"] if attempt % 2 == 0 else proxy_args
                        subprocess.run(["/usr/bin/curl", "--http1.1", "-sS", "-L", *route, "--connect-timeout", "12", "--max-time", "180", "--range", f"{offset}-{end}", "-D", str(headers), "-o", str(incoming), resolved], capture_output=True)
                        ranges = re.findall(r"content-range:\s*bytes (\d+)-(\d+)/(\d+)", headers.read_text().lower() if headers.exists() else "")
                        if ranges and tuple(map(int, ranges[-1])) == (offset, end, size) and incoming.exists() and incoming.stat().st_size <= end - offset + 1:
                            with partial.open("ab") as out, incoming.open("rb") as source:
                                while chunk := source.read(1024 * 1024):
                                    out.write(chunk)
                            incoming.unlink()
                    raise RuntimeError(f"Range download failed: {name}, {start}")

                begun, completed = time.monotonic(), 0
                with ThreadPoolExecutor(max_workers=24) as pool:
                    for future in as_completed([pool.submit(part, start) for start in range(0, size, block)]):
                        completed += future.result().stat().st_size
                        print(json.dumps({"file": name, "bytes": completed, "total": size, "seconds": round(time.monotonic() - begun, 1)}), flush=True)
                temporary = destination.with_suffix(destination.suffix + ".verified")
                with temporary.open("wb") as out:
                    for start in range(0, size, block):
                        with (parts / str(start)).open("rb") as source:
                            while chunk := source.read(1024 * 1024):
                                out.write(chunk)
                temporary.replace(destination)
                if not valid():
                    raise RuntimeError(f"Checksum mismatch: {name}")
                for child in parts.iterdir():
                    child.unlink()
                parts.rmdir()
        sha = sha256_file(destination)
        manifest["files"].append({"path": name, "size": size, "sha256": sha})
        print(f"verified {name} ({size} bytes)", flush=True)
    (root / "cyberaudio-manifest.json").write_text(json.dumps(manifest, indent=2))
    print("All model files verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--model", choices=["tts", "asr"], default="tts")
    arguments = parser.parse_args()
    download(arguments.root, arguments.proxy, ASR_REPO if arguments.model == "asr" else REPO,
             ASR_REVISION if arguments.model == "asr" else REVISION)
