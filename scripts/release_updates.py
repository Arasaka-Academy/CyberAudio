"""Build and publish immutable signed update assets, then permit feed deployment."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from fetch_sparkle_tools import fetch_tools

ROOT = Path(__file__).resolve().parents[1]
REPO = "Arasaka-Academy/CyberAudio"
NS = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}


def semantic(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Expected a numeric stable version")
    return tuple(int(part) for part in value.split("."))


def validate_version(tag, info):
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+)(-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", tag)
    if not match or match[1] != info["CFBundleShortVersionString"]:
        raise ValueError("Release tag must match the application's version")
    build = str(info["CFBundleVersion"])
    if not re.fullmatch(r"[1-9]\d*", build):
        raise ValueError("CFBundleVersion must be a positive integer")
    return int(build), bool(match[2])


def validate_advance(info, previous):
    if previous is None:
        if (info["CFBundleShortVersionString"], str(info["CFBundleVersion"])) != ("0.2.0", "4"):
            raise ValueError("Only bootstrap 0.2.0 build 4 may initialize an empty feed")
        return
    try:
        items = ET.fromstring(previous).findall("./channel/item")
        if not items:
            raise ValueError("Published feed contains no versions")
        for item in items:
            build = int(item.findtext("sparkle:version", namespaces=NS))
            version = semantic(item.findtext("sparkle:shortVersionString", namespaces=NS))
            if int(info["CFBundleVersion"]) <= build or semantic(info["CFBundleShortVersionString"]) <= version:
                raise ValueError("Stable feed must advance both version and build number")
    except (ET.ParseError, TypeError) as exc:
        raise ValueError("Published feed is invalid") from exc


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def validate_release(tag, info, metadata):
    _, prerelease = validate_version(tag, info)
    if metadata.get("draft") or metadata["prerelease"] != prerelease:
        raise ValueError("Release must be published and its prerelease flag must match the tag")


def release_metadata(tag):
    return json.loads(run(["gh", "api", f"repos/{REPO}/releases/tags/{tag}"], capture_output=True, text=True).stdout)


def check_release_history(info, tag):
    # Release manifests also guard against a stale Pages CDN response after a recent deployment.
    pages = json.loads(run(["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100"],
                          capture_output=True, text=True).stdout)
    for release in [value for page in pages for value in page]:
        if release["draft"] or release["prerelease"] or release["tag_name"] == tag:
            continue
        for asset in release["assets"]:
            if asset["name"] != "release.json":
                continue
            previous = json.loads(run(["gh", "api", "-H", "Accept: application/octet-stream",
                f"repos/{REPO}/releases/assets/{asset['id']}"], capture_output=True, text=True).stdout)
            if int(info["CFBundleVersion"]) <= int(previous["build"]) or semantic(info["CFBundleShortVersionString"]) <= semantic(previous["version"]):
                raise ValueError("Release history already contains this or a newer stable version")


def signing_args():
    return ["--ed-key-file", "-"] if os.environ.get("SPARKLE_EDDSA_PRIVATE_KEY") else ["--account", "local.cyberaudio.mac"]


def sign_run(tool, args):
    secret = os.environ.get("SPARKLE_EDDSA_PRIVATE_KEY")
    return run([tool, *signing_args(), *args], input=(secret.strip() + "\n").encode() if secret else None)


def validate_key(tools, info):
    if os.environ.get("SPARKLE_EDDSA_PRIVATE_KEY"):
        run(["swift", ROOT / "scripts/validate_signing_key.swift", ROOT / "packaging/Info.plist"])
    else:
        result = run([tools / "generate_keys", "--account", "local.cyberaudio.mac", "-p"], capture_output=True, text=True)
        if result.stdout.strip() != info["SUPublicEDKey"]:
            raise ValueError("Keychain public key does not match Info.plist")


def check_feed(info, tools, tag=None):
    if os.environ.get("GH_TOKEN") or tag:
        check_release_history(info, tag)
    try:
        request = urllib.request.Request(info["SUFeedURL"] + f"?release-check={time.time_ns()}", headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=30) as response:
            previous = response.read(2 * 1024 * 1024 + 1)
        if len(previous) > 2 * 1024 * 1024:
            raise ValueError("Published feed is too large")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        previous = None
    if previous is not None:
        with tempfile.TemporaryDirectory(prefix="cyberaudio-feed-") as directory:
            path = Path(directory) / "appcast.xml"
            path.write_bytes(previous)
            sign_run(tools / "sign_update", ["--verify", path])
    validate_advance(info, previous)
    print("Stable feed version guard passed")


def build(tag, notes):
    info = plistlib.loads((ROOT / "packaging/Info.plist").read_bytes())
    _, prerelease = validate_version(tag, info)
    tools = fetch_tools()
    validate_key(tools, info)
    run(["bash", "scripts/build_app.sh"])
    run(["bash", "scripts/build_dmg.sh", tag, "--reuse-app"])
    dist = ROOT / "dist"
    name = f"CyberAudio-{tag.removeprefix('v')}-macOS-arm64"
    with tempfile.TemporaryDirectory(prefix="cyberaudio-updates-") as directory:
        directory = Path(directory)
        archive = directory / (name + ".zip")
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", dist / "CyberAudio.app", archive])
        body = Path(notes).read_text() if notes else f"CyberAudio {info['CFBundleShortVersionString']}"
        (directory / (name + ".html")).write_text(
            "<p>更新将重启 CyberAudio，请先复制需要的文字并保存音频。</p><pre>" + html.escape(body) + "</pre>")
        options = ["--download-url-prefix", f"https://github.com/{REPO}/releases/download/{tag}/",
            "--embed-release-notes", "--maximum-deltas", "0", "--maximum-versions", "1"]
        if prerelease:
            options += ["--channel", "beta"]
        sign_run(tools / "generate_appcast", [*options, directory])
        feed = directory / "appcast.xml"
        sign_run(tools / "sign_update", ["--verify", feed])
        archive.replace(dist / archive.name)
        feed.replace(dist / "appcast.xml")
    archive = dist / (name + ".zip")
    (dist / (archive.name + ".sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
    paths = [dist / (name + suffix) for suffix in [".dmg", ".dmg.sha256", ".zip", ".zip.sha256"]] + [dist / "appcast.xml"]
    manifest = {"tag": tag, "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
        "prerelease": prerelease, "commit": run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    (dist / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Signed release archives and appcast verified")


def upload(tag):
    dist = ROOT / "dist"
    manifest = json.loads((dist / "release.json").read_text())
    if manifest["tag"] != tag:
        raise ValueError("Release manifest tag mismatch")
    data = release_metadata(tag)
    validate_release(tag, {"CFBundleShortVersionString": manifest["version"], "CFBundleVersion": manifest["build"]}, data)
    paths = [dist / name for name in manifest["artifacts"]] + [dist / "release.json"]
    for name, expected in manifest["artifacts"].items():
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Release artifact changed after signing")
    assets = {asset["name"]: asset for asset in data["assets"]}
    missing = [path for path in paths if path.name not in assets]
    # No --clobber: retries may fill missing assets, never replace an existing archive.
    if missing:
        run(["gh", "release", "upload", tag, "--repo", REPO, *missing])
    assets = {asset["name"]: asset for asset in release_metadata(tag)["assets"]}
    with tempfile.TemporaryDirectory(prefix="cyberaudio-upload-") as directory:
        for path in paths:
            asset = assets.get(path.name)
            if not asset or asset["size"] != path.stat().st_size:
                raise ValueError("Release asset missing or truncated")
            downloaded = Path(directory) / path.name
            with downloaded.open("wb") as output:
                run(["gh", "api", "-H", "Accept: application/octet-stream", f"repos/{REPO}/releases/assets/{asset['id']}"], stdout=output)
            if hashlib.sha256(downloaded.read_bytes()).digest() != hashlib.sha256(path.read_bytes()).digest():
                raise ValueError("Uploaded asset checksum mismatch")
    print("All public Release assets downloaded and verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "upload", "check-feed", "validate-release"])
    parser.add_argument("--tag")
    parser.add_argument("--notes")
    args = parser.parse_args()
    info = plistlib.loads((ROOT / "packaging/Info.plist").read_bytes())
    if args.command == "check-feed":
        check_feed(info, fetch_tools(), args.tag)
    elif args.command == "build":
        build(args.tag or "v" + info["CFBundleShortVersionString"], args.notes)
    elif not args.tag:
        parser.error("--tag is required")
    elif args.command == "validate-release":
        validate_release(args.tag, info, release_metadata(args.tag))
    else:
        upload(args.tag)


if __name__ == "__main__":
    main()
