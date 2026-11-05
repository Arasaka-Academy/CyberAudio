"""Create two isolated real .apps and signed feeds; never touches /Applications."""
import argparse
import base64
import json
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from fetch_sparkle_tools import fetch_tools


def run(args, **options):
    return subprocess.run([str(v) for v in args], check=True, **options)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=["valid", "tampered-feed", "corrupt-archive", "wrong-signature", "current", "older"], default="valid")
    parser.add_argument("--manual", action="store_true", help="Exercise Sparkle's foreground download UI")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    data = root / "data"
    for child in ["config", "runtime", "reports", "models"]:
        (data / child).mkdir(parents=True)
    api, feed_port = port(), port()
    (data / "config/service.json").write_text(json.dumps({"asr_dir": str(data / "models"), "tts_dir": str(data / "models"),
        "ffmpeg": "/opt/homebrew/bin/ffmpeg", "port": api, "https_url": ""}))
    (data / "config/api-token").write_text("update-fixture-token-" * 4)
    (data / "config/offline.sb").write_text('(version 1)\n(allow default)\n')
    for relative in ["models/preserve.txt", "runtime/preserve.txt", "config/preserve.txt"]:
        (data / relative).write_text("CyberAudio update preservation sentinel\n")
    feed = root / "feed"
    feed.mkdir()
    apps = []
    next_version, next_build = {"current": ("0.2.0", "4"), "older": ("0.1.9", "3")}.get(args.scenario, ("0.2.1", "5"))
    native_scratch = PROJECT / ".context/native-build"
    run(["swift", "build", "--build-system", "native", "--package-path", PROJECT / "Native", "--scratch-path", native_scratch,
         "-c", "release", "--skip-update", "--product", "CyberAudioFixture"])
    native_bin = subprocess.check_output(["swift", "build", "--build-system", "native", "--package-path", str(PROJECT / "Native"),
         "--scratch-path", str(native_scratch), "-c", "release", "--show-bin-path"], text=True).strip()
    for name, version, build in [("installed", "0.2.0", "4"), ("next", next_version, next_build)]:
        app = root / name / "CyberAudio.app"
        app.parent.mkdir()
        run(["ditto", PROJECT / "dist/CyberAudio.app", app])
        plist = app / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        suffix = re.sub(r"[^a-z0-9-]", "-", root.name.lower())
        info.update({"CFBundleIdentifier": f"local.cyberaudio.updater-test.{suffix}", "CFBundleName": "CyberAudio Update Test",
            "CFBundleShortVersionString": version, "CFBundleVersion": build,
            "SUAutomaticallyUpdate": not args.manual,
            "SUFeedURL": f"http://127.0.0.1:{feed_port}/appcast.xml"})
        plist.write_bytes(plistlib.dumps(info))
        helper = app / "Contents/Helpers/CyberAudioService"
        helper.write_bytes((Path(native_bin) / "CyberAudioFixture").read_bytes())
        run(["codesign", "--force", "--sign", "-", helper])
        run(["codesign", "--force", "--sign", "-", app])
        run(["codesign", "--verify", "--deep", "--strict", app])
        apps.append(app)
    archive = feed / "CyberAudio-test-5.zip"
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", apps[1], archive])
    tools = fetch_tools()
    run([tools / "generate_appcast", "--account", "local.cyberaudio.mac", "--maximum-deltas", "0",
        "--download-url-prefix", f"http://127.0.0.1:{feed_port}/", feed])
    run([tools / "sign_update", "--account", "local.cyberaudio.mac", "--verify", feed / "appcast.xml"])
    if args.scenario == "tampered-feed":
        path = feed / "appcast.xml"
        path.write_bytes(path.read_bytes().replace(b"<title>", b"<title>unsigned change ", 1))
    elif args.scenario == "corrupt-archive":
        damaged = bytearray(archive.read_bytes())
        damaged[len(damaged) // 2] ^= 1
        archive.write_bytes(damaged)
    elif args.scenario == "wrong-signature":
        tree = ET.parse(feed / "appcast.xml")
        tree.find("./channel/item/enclosure").set("{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature", base64.b64encode(bytes(64)).decode())
        tree.write(feed / "appcast.xml", encoding="utf-8", xml_declaration=True)
        run([tools / "sign_update", "--account", "local.cyberaudio.mac", feed / "appcast.xml"])
    state = {"app": str(apps[0]), "data": str(data), "feed": str(feed), "api_port": api, "feed_port": feed_port}
    (root / "fixture.json").write_text(json.dumps(state, indent=2))
    print(json.dumps(state))


if __name__ == "__main__":
    main()
