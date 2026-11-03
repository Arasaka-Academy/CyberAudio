"""On-target acceptance: terminate the owning app during actual streamed inference."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request


ROOT = Path.home() / "Library/Application Support/CyberAudio"
DRIVER = "/Applications/CuaDriver.app/Contents/MacOS/cua-driver"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:8765"


def ready():
    try:
        with OPENER.open(BASE + "/ready", timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def app_pid():
    result = subprocess.check_output([
        "/usr/bin/pgrep", "-f", "^/Applications/CyberAudio.app/Contents/MacOS/CyberAudio$"
    ], text=True)
    pids = [int(value) for value in result.split()]
    assert len(pids) == 1, "Expected exactly one production app"
    return pids[0]


def restart(driver):
    result = subprocess.run([
        driver, "launch_app", '{"bundle_id":"local.cyberaudio.mac"}'
    ], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, "Background app launch failed"
    begin = time.monotonic()
    for _ in range(300):
        state = ready()
        if state and state["status"] == "ready":
            return {"instance": state["instance"], "ready_after_launch_ms": round((time.monotonic() - begin) * 1000, 1)}
        time.sleep(0.2)
    raise RuntimeError("App did not recover")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", default=DRIVER)
    args = parser.parse_args()
    token = (ROOT / "config/api-token").read_text().strip()
    report = {"started": time.time(), "steps": []}
    for name, sig in [("active_normal_sigterm", signal.SIGTERM), ("active_forced_app_crash", signal.SIGKILL)]:
        state = ready()
        assert state and state["status"] == "ready" and not state["busy"]
        payload = json.dumps({
            "input": "这是本地生命周期验收。我们将检查正在生成语音时退出应用，推理引擎是否一起结束。" * 4,
            "voice": "serena", "language": "chinese", "response_format": "pcm", "stream": True,
        }).encode()
        request = urllib.request.Request(BASE + "/v1/audio/speech", data=payload, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
        })
        with OPENER.open(request, timeout=10) as stream:
            first = stream.read(512)
            assert len(first) == 512
            request_id = stream.headers["X-Request-ID"]
            active = ready()
            assert active and active["busy"], "Inference finished before exit test"
            pid = app_pid()
            # The direct Python child is the model process; exclude our verification script.
            children = subprocess.check_output(["/usr/bin/pgrep", "-P", str(pid)], text=True)
            child_pids = [int(value) for value in children.split()]
            assert len(child_pids) == 1, "Expected one directly owned backend"
            begin = time.monotonic()
            os.kill(pid, sig)
            for _ in range(150):
                try:
                    with socket.create_connection(("127.0.0.1", 8765), timeout=0.1):
                        pass
                except OSError:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Listener survived app exit")
            elapsed = round((time.monotonic() - begin) * 1000, 1)
        for _ in range(100):
            if all(subprocess.run(["/bin/kill", "-0", str(value)], capture_output=True).returncode != 0 for value in child_pids):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Model process survived app exit")
        time.sleep(3)
        assert ready() is None, "Backend resurrected without its app"
        recovery = restart(args.driver)
        assert recovery["instance"] != state["instance"]
        record = {"kind": name, "request_id": request_id, "busy_at_exit": True,
                  "listener_stopped_ms": elapsed, "model_process_gone": True,
                  "no_resurrection_after_s": 3, "restart": recovery}
        report["steps"].append(record)
        (ROOT / "reports/active-exit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(record), flush=True)
    report["finished"] = time.time()
    (ROOT / "reports/active-exit.json").write_text(json.dumps(report, indent=2))
    print("ACTIVE_EXIT_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
