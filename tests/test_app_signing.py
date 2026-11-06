"""Distribution must fail closed; local signatures must cover nested code."""
import json
import plistlib
from pathlib import Path
import subprocess
import sys
import hashlib
import ssl

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sign_app


def test_distribution_requires_identity_team_and_notary_profile():
    for env in [{"CYBERAUDIO_SIGN_MODE": "distribution"},
                {"CYBERAUDIO_SIGN_MODE": "distribution", "CYBERAUDIO_SIGN_IDENTITY": "-"},
                {"CYBERAUDIO_SIGN_MODE": "development", "CYBERAUDIO_SIGN_IDENTITY": "test", "CYBERAUDIO_NOTARY_PROFILE": "profile"},
                {"CYBERAUDIO_SIGN_IDENTITY": "test"}]:
        with pytest.raises(ValueError):
            sign_app.SigningConfiguration.read(env)


def test_distribution_rejects_development_identity_and_wrong_team(monkeypatch):
    config = sign_app.SigningConfiguration("distribution", "A" * 40, "TEAM123", "profile")
    for identity in [f'1) {"A" * 40} "Apple Development: Test (TEAM123)"',
                     f'1) {"A" * 40} "Developer ID Application: Test (OTHER)"']:
        monkeypatch.setattr(sign_app, "run", lambda *_args, **_kw: subprocess.CompletedProcess([], 0, identity, ""))
        with pytest.raises(ValueError):
            sign_app.validate_identity(config)


def test_development_team_comes_from_certificate_ou_not_common_name(tmp_path, monkeypatch):
    key, certificate = tmp_path / "test.key", tmp_path / "test.pem"
    name = "Apple Development: Local Test (MEMBER999)"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", f"/OU=TEAM123/CN={name}"],
                   check=True, capture_output=True)
    pem = certificate.read_text()
    digest = hashlib.sha1(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    original = sign_app.run

    def run(args, **kwargs):
        if args[:2] == ["security", "find-identity"]:
            return subprocess.CompletedProcess(args, 0, f'1) {digest} "{name}"', "")
        if args[:2] == ["security", "find-certificate"]:
            return subprocess.CompletedProcess(args, 0, pem, "")
        return original(args, **kwargs)

    monkeypatch.setattr(sign_app, "run", run)
    sign_app.validate_identity(sign_app.SigningConfiguration("development", digest, "TEAM123", ""))
    with pytest.raises(ValueError):
        sign_app.validate_identity(sign_app.SigningConfiguration("development", digest, "MEMBER999", ""))


def test_rejected_notarization_never_staples_or_assesses(tmp_path, monkeypatch):
    config = sign_app.SigningConfiguration("distribution", "identity", "TEAM123", "profile")
    commands = []

    def run(args, **_kwargs):
        commands.append([str(value) for value in args])
        return subprocess.CompletedProcess(args, 0, json.dumps({"status": "Invalid", "id": "test"}), "")

    monkeypatch.setattr(sign_app, "run", run)
    monkeypatch.setattr(sign_app, "verify", lambda *_args: None)
    with pytest.raises(ValueError):
        sign_app.notarize(tmp_path / "Test.app", config)
    assert any("notarytool" in command for command in commands)
    assert not any("stapler" in command or "spctl" in command for command in commands)


@pytest.mark.parametrize("failure", ["stapler", "spctl"])
def test_distribution_verification_rejects_unaccepted_reused_app(tmp_path, monkeypatch, failure):
    config = sign_app.SigningConfiguration("distribution", "identity", "TEAM123", "profile")
    commands = []

    def run(args, **_kwargs):
        commands.append([str(value) for value in args])
        if failure in args:
            raise subprocess.CalledProcessError(1, args)
        metadata = "TeamIdentifier=TEAM123\nAuthority=Developer ID Application: Test\nflags=0x10000(runtime)\n"
        return subprocess.CompletedProcess(args, 0, "", metadata)

    monkeypatch.setattr(sign_app, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        sign_app.verify(tmp_path / "Reused.app", config)
    assert any("stapler" in command for command in commands)
    assert any("spctl" in command for command in commands) == (failure == "spctl")


def test_local_bundle_signature_covers_nested_helper_and_detects_tampering(tmp_path):
    app = tmp_path / "Test.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Helpers").mkdir()
    (contents / "Resources").mkdir()
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "Test", "CFBundleIdentifier": "local.cyberaudio.signing-test",
        "CFBundlePackageType": "APPL", "CFBundleVersion": "1"}))
    for binary in [contents / "MacOS/Test", contents / "Helpers/CyberAudioAssets", contents / "Helpers/CyberAudioService"]:
        subprocess.run(["xcrun", "clang", "-x", "c", "-", "-o", str(binary)], input=b"int main(void) { return 0; }\n", check=True, capture_output=True)
    sign_app.sign(app, sign_app.SigningConfiguration.read({}))
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True, capture_output=True)
    (contents / "Resources/tampered.txt").write_text("changed after signing")
    assert subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True).returncode != 0
