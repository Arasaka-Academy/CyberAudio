"""An acceptance driver that terminates Apps must reject unsafe setup first."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import plistlib
import socket
import struct
import subprocess
import threading

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


@pytest.mark.parametrize("failure_mode", ["terminal_record", "wav_http_error", "pcm_transport_error"])
def test_soak_failure_keeps_request_cause_and_workload_context(tmp_path, failure_mode):
    """Exercise the real Swift HTTP path without launching an App or models."""
    prefix = (PROJECT / "scripts/verify_native.swift").read_text().split("@main enum NativeValidation")[0]
    source = tmp_path / "Probe.swift"
    source.write_text(prefix + r'''
@main enum ReportingProbe {
    static func main() {
        let args = CommandLine.arguments
        let root = URL(fileURLWithPath: args[2]), corpus = URL(fileURLWithPath: args[3])
        do {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: corpus.appendingPathComponent("corpus.json"))) as! [[String: Any]]
            _ = try soak(URL(string: args[1])!, root: root, corpus: corpus, rows: rows,
                         token: String(repeating: "x", count: 64), instance: "reporting-fixture",
                         processID: getpid(), seconds: 30, drain: {})
            exit(2)
        } catch { fputs("EXPECTED FAILURE: \(error)\n", stderr); exit(1) }
    }
}
''')
    # The soak preflight checks its RSS target executable name. This executable
    # is just a reporting probe; it has no inference dependencies or listeners.
    executable = tmp_path / "CyberAudioService"
    subprocess.run(["swiftc", "-parse-as-library", "-swift-version", "5", "-target",
                    "arm64-apple-macos26.0", str(source), "-o", str(executable)],
                   check=True, capture_output=True, timeout=90)
    root, corpus = tmp_path / "owned-data", tmp_path / "corpus"
    (root / "reports").mkdir(parents=True)
    (root / "tmp").mkdir()
    corpus.mkdir()
    pcm = bytes(4)
    audio = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ",
                        16, 1, 1, 24000, 48000, 2, 16, b"data", len(pcm)) + pcm
    (corpus / "audio.wav").write_bytes(audio)
    (corpus / "corpus.json").write_text(json.dumps([{
        "id": "fixture-input", "audio": "audio.wav", "audio_sha256": hashlib.sha256(audio).hexdigest(),
        "language": "zh", "reference": "受控回归",
    }]))

    class Handler(BaseHTTPRequestHandler):
        speech_requests = 0

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status = 200
            if self.path == "/v1/audio/transcriptions":
                body = json.dumps({"text": "受控回归", "request_id": "fixture-asr", "processing_ms": 1}).encode()
                content_type, identity = "application/json", "fixture-asr"
            elif self.path == "/v1/audio/speech":
                type(self).speech_requests += 1
                body, content_type, identity = audio, "audio/wav", "fixture-tts"
                if failure_mode == "wav_http_error":
                    status, content_type = 422, "application/json"
                    body = json.dumps({"error": {"code": "generation_limit", "message": "受控长度错误"}}).encode()
                elif failure_mode == "pcm_transport_error":
                    if type(self).speech_requests == 1:
                        identity = "fixture-tts-1"
                    else:
                        body, content_type = bytes(8), "audio/pcm"
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            interrupted = failure_mode == "pcm_transport_error" and content_type == "audio/pcm"
            self.send_header("Content-Length", str(len(body) + (100 if interrupted else 0)))
            self.send_header("X-Request-ID", identity)
            self.end_headers()
            self.wfile.write(body)
            if interrupted:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True

        def do_GET(self):
            if self.path == "/v1/requests/fixture-asr":
                value = {"request_id": "fixture-asr", "status": "completed", "kind": "stt"}
            elif self.path == "/v1/requests/fixture-tts":
                value = {"request_id": "fixture-tts", "status": "failed", "kind": "tts", "code": "generation_limit"}
            elif self.path == "/v1/requests/fixture-tts-1":
                value = {"request_id": "fixture-tts-1", "status": "completed", "kind": "tts",
                         "metal_active_bytes": 1, "metal_peak_bytes": 2,
                         "process_peak_rss_bytes": 3, "processing_ms": 4}
            else:
                self.send_error(404)
                return
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([str(executable), f"http://127.0.0.1:{server.server_port}", str(root), str(corpus)],
                                    capture_output=True, text=True, timeout=30)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert result.returncode == 1
    report = json.loads((root / "reports/soak.json").read_text())
    assert report["status"] == "failed"
    assert report["failed_request"]["request_id"] == "fixture-tts"
    assert report["failed_request"]["code"] == "generation_limit"
    assert report["failed_request_http_status"] == 200
    assert report["failure"] and report["finished"]
    assert report["active_workload"] == {
        "cycle": 2 if failure_mode == "pcm_transport_error" else 1, "corpus_id": "fixture-input",
        "voice": "vivian" if failure_mode == "pcm_transport_error" else "serena",
        "format": "pcm" if failure_mode == "pcm_transport_error" else "wav",
    }
    if failure_mode == "wav_http_error":
        assert report["failed_response"]["http_status"] == 422
        assert report["failed_response"]["json"]["error"]["code"] == "generation_limit"
    if failure_mode == "pcm_transport_error":
        assert report["failed_response"]["http_status"] == 200
        assert report["failed_response"]["transport_error"]["code"] == -1005
