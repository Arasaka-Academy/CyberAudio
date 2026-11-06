"""Sign owned App payloads and optionally notarize a Developer ID build."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import ssl
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MACHO = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


@dataclass(frozen=True)
class SigningConfiguration:
    mode: str = "adhoc"
    identity: str = "-"
    team: str = ""
    profile: str = ""

    @classmethod
    def read(cls, env):
        mode = env.get("CYBERAUDIO_SIGN_MODE", "adhoc")
        identity = env.get("CYBERAUDIO_SIGN_IDENTITY", "").strip()
        team = env.get("CYBERAUDIO_TEAM_ID", "").strip()
        profile = env.get("CYBERAUDIO_NOTARY_PROFILE", "").strip()
        if mode not in {"adhoc", "development", "distribution"}:
            raise ValueError("Unknown CYBERAUDIO_SIGN_MODE")
        if mode == "adhoc":
            if identity not in {"", "-"} or profile:
                raise ValueError("Select development or distribution explicitly when supplying signing credentials")
            return cls()
        if not identity or identity == "-" or not team:
            raise ValueError("Certificate signing requires CYBERAUDIO_SIGN_IDENTITY and CYBERAUDIO_TEAM_ID")
        if mode == "distribution" and not profile:
            raise ValueError("Distribution requires CYBERAUDIO_NOTARY_PROFILE; notarization cannot be skipped")
        if mode == "development" and profile:
            raise ValueError("Apple Development signatures cannot be distributed through Developer ID notarization")
        return cls(mode, identity, team, profile)


def run(args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, capture_output=True, text=True, **kwargs)


def validate_identity(config):
    if config.mode == "adhoc":
        return
    identities = run(["security", "find-identity", "-v", "-p", "codesigning"], timeout=30).stdout
    expected = "Developer ID Application:" if config.mode == "distribution" else "Apple Development:"
    for digest, name in re.findall(r'\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"', identities):
        if config.identity == name or config.identity.upper() == digest.upper():
            if name.startswith(expected) and certificate_team(name, digest) == config.team:
                return
            break
    raise ValueError("The selected valid signing identity does not match the required certificate type and team")


def certificate_team(name, digest):
    # Apple Development's common-name suffix can identify the developer rather
    # than the team. Read the exact certificate's OU instead of guessing from CN.
    certificates = run(["security", "find-certificate", "-a", "-c", name, "-p"], timeout=30).stdout
    for pem in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", certificates, re.S):
        if hashlib.sha1(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper() != digest.upper():
            continue
        subject = run(["openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253"], input=pem, timeout=30).stdout
        match = re.search(r"(?:^|,)OU=([A-Z0-9]+)(?:,|$)", subject)
        if match:
            return match[1]
    raise ValueError("Cannot verify the selected signing certificate's team")


def targets(app, full):
    helpers = app / "Contents/Helpers"
    if full:
        result = []
        for path in app.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir() and path.suffix in {".app", ".xpc", ".framework", ".bundle"}:
                result.append(path)
            elif path.is_file():
                with path.open("rb") as source:
                    if source.read(4) in MACHO:
                        result.append(path)
    else:
        result = [helpers / "CyberAudioService", helpers / "CyberAudioAssets", *helpers.glob("*.bundle")]
    return sorted(result, key=lambda path: (-len(path.parts), str(path))) + [app]


def sign(app, config):
    if not (app / "Contents/Info.plist").is_file():
        raise ValueError("Expected a complete macOS App bundle")
    validate_identity(config)
    for target in targets(app, config.mode != "adhoc"):
        args = ["codesign", "--force", "--sign", config.identity]
        if config.mode != "adhoc":
            args += ["--options", "runtime", "--timestamp" if config.mode == "distribution" else "--timestamp=none"]
            if "Sparkle.framework" in target.parts:
                args += ["--preserve-metadata=identifier,entitlements"]
            if target == app:
                args += ["--entitlements", ROOT / "packaging/App.entitlements"]
        run([*args, target], timeout=120)
    # Signing precedes notarization. Distribution consumers use verify()'s
    # default ticket/Gatekeeper checks, including --reuse-app DMG builds.
    verify(app, config, False)
    print(f"App and nested code verified ({config.mode})")


def verify(app, config, require_notarization=True):
    run(["codesign", "--verify", "--deep", "--strict", app], timeout=120)
    if config.mode == "adhoc":
        return
    output = run(["codesign", "--display", "--verbose=4", app], timeout=30)
    metadata = output.stdout + output.stderr
    authority = "Developer ID Application:" if config.mode == "distribution" else "Apple Development:"
    if f"TeamIdentifier={config.team}\n" not in metadata or f"Authority={authority}" not in metadata or "runtime" not in metadata:
        raise ValueError("App signing authority, team or hardened runtime does not match the selected mode")
    if config.mode == "distribution" and require_notarization:
        run(["xcrun", "stapler", "validate", app], timeout=60)
        run(["spctl", "--assess", "--type", "execute", app], timeout=120)


def notarize(app, config):
    if config.mode != "distribution":
        return
    verify(app, config, False)
    with tempfile.TemporaryDirectory(prefix="CyberAudio-notary-") as directory:
        archive = Path(directory) / "CyberAudio.zip"
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive], timeout=300)
        keychain = os.environ.get("CYBERAUDIO_SIGN_KEYCHAIN")
        response = run(["xcrun", "notarytool", "submit", archive, "--keychain-profile", config.profile,
                        *(["--keychain", keychain] if keychain else []),
                        "--wait", "--output-format", "json"], timeout=2400)
        result = json.loads(response.stdout)
        if result.get("status") != "Accepted":
            raise ValueError("Apple did not accept notarization; distribution artifacts must not be published")
    run(["xcrun", "stapler", "staple", app], timeout=180)
    verify(app, config)
    print("Notarization accepted, ticket stapled and Gatekeeper assessment passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "sign", "notarize", "verify"])
    parser.add_argument("app", type=Path, nargs="?", default=ROOT / "dist/CyberAudio.app")
    args = parser.parse_args()
    try:
        config = SigningConfiguration.read(os.environ)
        if args.command == "preflight":
            validate_identity(config)
        else:
            {"sign": sign, "notarize": notarize, "verify": verify}[args.command](args.app.resolve(), config)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # codesign/notarytool output may contain keychain paths or account details.
        parser.exit(1, f"Signing failed: {error if isinstance(error, ValueError) else 'system signing or notarization command failed; check local credentials and keychain access'}\n")


if __name__ == "__main__":
    main()
