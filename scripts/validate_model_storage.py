#!/usr/bin/env python3
"""Exercise recoverable model migration on a disposable mounted APFS volume."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import uuid

PROJECT = Path(__file__).resolve().parents[1]


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Use a fresh evidence directory")
    output.mkdir(parents=True)
    models = json.loads((PROJECT / "Native/Sources/CyberAudioCore/ModelCatalog.lock.json").read_text())["models"]
    model = next(model for model in models if model["id"] == "qwen3-asr-1.7b")
    asset = next(file for file in model["files"] if file["path"].endswith("/README.md"))
    large = max(model["files"], key=lambda file: file["size"])
    reference = args.models / model["repository"].split("/")[-1] / "README.md"
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == asset["sha256"]
    summary = {"host": socket.gethostname(), "cases": {}}
    image = output / "model-volume.sparseimage"
    volume = None

    def command(root, name, *options, success=True):
        result = subprocess.run([str(args.assets), name, "--root", str(root), "--json", "true", *options],
                                capture_output=True, text=True, timeout=90)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        if success:
            assert result.returncode == 0, records
        else:
            assert result.returncode != 0, records
        return records[-1] if records else {}

    def fixture(name, partial_bytes=1024):
        root = output / name
        command(root, "prepare", "--port", "19768")
        config = root / "config/service.json"
        model_root = Path(json.loads(config.read_text())["asr_dir"])
        model_root.mkdir(parents=True, exist_ok=True)
        registered = model_root / "README.md"
        shutil.copyfile(reference, registered)
        notes = model_root / "user-notes.txt"
        notes.write_text("preserve user content")
        partial = root / "tmp/downloads" / (large["sha256"] + ".partial")
        partial.parent.mkdir(parents=True)
        with partial.open("wb") as file:
            file.truncate(partial_bytes)
        return root, config.read_bytes(), registered, partial, notes

    def attach():
        result = subprocess.run(["hdiutil", "attach", "-nobrowse", "-plist", str(image)], check=True, capture_output=True)
        entities = plistlib.loads(result.stdout)["system-entities"]
        return Path(next(entity["mount-point"] for entity in entities if "mount-point" in entity))

    def detach(force=False):
        nonlocal volume
        if volume is not None:
            subprocess.run(["hdiutil", "detach", *(["-force"] if force else []), str(volume)],
                           check=True, capture_output=True)
            volume = None

    try:
        subprocess.run(["hdiutil", "create", "-size", "512m", "-type", "SPARSE", "-fs", "APFS",
                        "-volname", "CyberAudio-Models-" + uuid.uuid4().hex[:8], str(image)], check=True, capture_output=True)
        volume = attach()
        root, original, registered, partial, notes = fixture("cross-volume")
        destination = volume / "models"
        destination.mkdir()
        extra = destination / "user-extra.txt"
        extra.write_text("keep target content")
        target = destination / model["repository"].split("/")[-1] / "README.md"
        moved_partial = destination / ".downloads" / partial.name
        command(root, "move-models", "--destination", str(destination), "--transaction", "true")
        assert registered.stat().st_dev != target.stat().st_dev
        assert hashlib.sha256(target.read_bytes()).hexdigest() == asset["sha256"]
        assert moved_partial.read_bytes() == partial.read_bytes()
        command(root, "rollback-model-change")
        assert (root / "config/service.json").read_bytes() == original and registered.exists() and not target.exists()
        assert extra.read_text() == "keep target content"
        command(root, "move-models", "--destination", str(destination), "--transaction", "true")
        # A crash after the durable commit marker must finish cleanup rather
        # than restoring a source tree whose cleanup has already started.
        journal = root / "config/model-change.json"
        change = json.loads(journal.read_text())
        change["committed"] = True
        journal.write_text(json.dumps(change))
        registered.unlink()
        command(root, "prepare")
        assert json.loads((root / "config/service.json").read_text())["models_dir"] == str(destination)
        assert not journal.exists() and not partial.exists() and moved_partial.exists() and notes.exists() and extra.exists()
        summary["cases"]["cross_volume_copy_hash_rollback_commit_recovery"] = True

        root, original, registered, partial, notes = fixture("conflict")
        conflict_root = volume / "conflicting-models"
        conflict = conflict_root / model["repository"].split("/")[-1] / "README.md"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("user conflict")
        failure = command(root, "move-models", "--destination", str(conflict_root), success=False)
        assert failure["code"] == "model_conflict" and conflict.read_text() == "user conflict"
        assert (root / "config/service.json").read_bytes() == original and registered.exists()
        summary["cases"]["conflicting_target_preserved"] = True

        root, original, registered, partial, notes = fixture("space", 350 * 1024 * 1024)
        failure = command(root, "move-models", "--destination", str(volume / "too-small"), success=False)
        assert failure["code"] == "insufficient_storage" and (root / "config/service.json").read_bytes() == original
        assert registered.exists() and partial.exists()
        summary["cases"]["insufficient_space_keeps_source"] = True

        root, original, registered, partial, notes = fixture("permissions")
        read_only = volume / "read-only"
        read_only.mkdir(mode=0o500)
        try:
            command(root, "move-models", "--destination", str(read_only), success=False)
            assert (root / "config/service.json").read_bytes() == original and registered.exists() and partial.exists()
        finally:
            read_only.chmod(0o700)
        summary["cases"]["permission_error_restores_config"] = True

        for mode in ["cancel", "crash"]:
            root, original, registered, partial, notes = fixture(mode, 128 * 1024 * 1024)
            destination = volume / mode
            process = subprocess.Popen([str(args.assets), "move-models", "--root", str(root), "--json", "true",
                                        "--destination", str(destination), "--transaction", "true"],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                for line in process.stdout:
                    if json.loads(line).get("event") == "progress":
                        process.terminate() if mode == "cancel" else process.kill()
                        break
                process.wait(timeout=30)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
            command(root, "prepare")
            assert (root / "config/service.json").read_bytes() == original
            assert registered.exists() and partial.stat().st_size == 128 * 1024 * 1024 and notes.exists()
            assert not (root / "config/model-change.json").exists()
            assert not (destination / ".downloads" / partial.name).exists()
            summary["cases"][mode + "_during_copy_restores_source"] = {"passed": True, "exit_code": process.returncode}

        root, original, registered, partial, notes = fixture("disconnect-during-copy", 128 * 1024 * 1024)
        destination = volume / "disconnect-during-copy"
        process = subprocess.Popen([str(args.assets), "move-models", "--root", str(root), "--json", "true",
                                    "--destination", str(destination), "--transaction", "true"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        disconnected = False
        try:
            for line in process.stdout:
                if json.loads(line).get("event") == "progress":
                    os.kill(process.pid, signal.SIGSTOP)
                    os.waitpid(process.pid, os.WUNTRACED)
                    old_volume = volume
                    detach(force=True)
                    disconnected = True
                    os.kill(process.pid, signal.SIGCONT)
                    break
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
        assert disconnected and process.returncode != 0
        command(root, "prepare")
        assert (root / "config/service.json").read_bytes() == original
        assert registered.exists() and partial.stat().st_size == 128 * 1024 * 1024 and notes.exists()
        assert not old_volume.exists(), "An absent model volume must never be recreated"
        volume = attach()
        summary["cases"]["forced_disconnect_during_copy_restores_source"] = True

        root, original, registered, partial, notes = fixture("disconnect")
        destination = volume / "disconnected"
        command(root, "move-models", "--destination", str(destination), "--transaction", "true")
        old_volume = volume
        detach()
        command(root, "rollback-model-change")
        assert (root / "config/service.json").read_bytes() == original and registered.exists() and partial.exists()
        assert not old_volume.exists()
        volume = attach()
        destination = volume / "disconnected"
        existing_target = destination / model["repository"].split("/")[-1] / "README.md"
        assert hashlib.sha256(existing_target.read_bytes()).hexdigest() == asset["sha256"]
        command(root, "move-models", "--destination", str(destination), "--transaction", "true")
        command(root, "prepare")
        assert (root / "config/service.json").read_bytes() == original and existing_target.exists() and notes.exists()
        summary["cases"]["disconnected_volume_rollback_and_reconnect"] = True

        root, original, registered, partial, notes = fixture("delete-inactive")
        config_file = root / "config/service.json"
        config = json.loads(original)
        config.update(asr_model_id="qwen3-asr-0.6b-4bit", asr_dir=str(root / "models/other-asr"))
        config_file.write_text(json.dumps(config))
        saved = config_file.read_bytes()
        command(root, "delete-model", "--model", model["id"])
        assert not registered.exists() and not partial.exists() and notes.exists()
        assert config_file.read_bytes() == saved
        summary["cases"]["inactive_delete_preserves_extras_and_selection"] = True
        summary["passed"] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    finally:
        detach()
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assets", type=Path, default=PROJECT / ".context/native-build/arm64-apple-macosx/release/CyberAudioAssets")
    run(parser.parse_args())
