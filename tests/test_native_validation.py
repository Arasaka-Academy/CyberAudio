"""An acceptance driver that terminates Apps must reject unsafe setup first."""
from pathlib import Path
import plistlib
import subprocess

import pytest


PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    executable = tmp_path_factory.mktemp("native-validation") / "verify-native"
    subprocess.run(
        ["swiftc", "-parse-as-library", "-swift-version", "5", "-target",
         "arm64-apple-macos26.0", str(PROJECT / "scripts/verify_native.swift"),
         "-o", str(executable)],
        check=True, capture_output=True, timeout=90,
    )
    return executable


def invoke(driver, directory, identifier, extra):
    contents = directory / "Test.app/Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}))
    root = directory / "data"
    result = subprocess.run(
        [str(driver), "--app", str(contents.parent), "--root", str(root),
         "--models", str(directory / "models"), "--corpus", str(directory / "corpus"),
         "--port", "18868", *extra],
        capture_output=True, text=True, timeout=10,
    )
    return result, root


@pytest.mark.parametrize(("extra", "message"), [
    ([], "production identity is forbidden"),
    (["--soak-seconds", "0"], "must be between"),
    (["--soak-seconds", "-1"], "must be between"),
    (["--soak-seconds", "NaN"], "must be between"),
    (["--soak-seconds", "86401"], "must be between"),
    (["--soak-seconds", "1", "--phase", "lifecycle"], "requires the inference phase"),
])
def test_rejected_setup_cannot_create_data_or_launch_a_helper(driver, tmp_path, extra, message):
    identifier = plistlib.loads((PROJECT / "packaging/Info.plist").read_bytes())["CFBundleIdentifier"]
    result, root = invoke(driver, tmp_path, identifier, extra)
    assert result.returncode == 1 and message in result.stderr
    assert not root.exists()


def test_separate_identity_advances_to_the_isolated_asset_stage(driver, tmp_path):
    result, root = invoke(driver, tmp_path, "local.cyberaudio.validation.test", [])
    # This metadata-only App intentionally has no helper or models. Its private
    # marker proves the valid setup advanced past preflight without inference.
    assert result.returncode == 1
    assert (root / ".native-validation").is_file()
