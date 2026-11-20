"""Exercise packaged IPC + local inference with Python/Node removed from PATH."""
import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
executable = bootstrap.ROOT / "apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe"
env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
result = {"standalone_worker": False, "note_verified": False, "native_generation": "SKIPPED"}
with tempfile.TemporaryDirectory() as temp:
    child = subprocess.Popen([str(executable), "--data-dir", str(Path(temp) / "data"), "--runtime-dir", str(bootstrap.ROOT / "runtime")],
                             cwd=temp, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        def call(method, **params):
            message = {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params}
            child.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            child.stdin.flush()
            line = child.stdout.readline()
            if not line:
                raise RuntimeError(f"Packaged worker exited: {child.stderr.read()}")
            response = json.loads(line)
            if response.get("id") != message["id"] or "error" in response:
                raise RuntimeError(str(response))
            return response["result"]

        call("system.snapshot")
        result["standalone_worker"] = True
        note = call("goal.submit", goal="/note Standalone worker: Привет", request_id="packaged-smoke")
        assert note["status"] == "COMPLETED"
        result["note_verified"] = call("artifact.read", artifact_id=note["id"])["verified"]
        call("workspace.write", path="console_smoke.py", text="import sys\nprint('Nexus bundled Python '+sys.argv[1])\n", expected_hash=None)
        command = call("terminal.start", tool="python", args=["console_smoke.py", "READY"], allow_execute=True, timeout=15)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            command = call("terminal.status", run_id=command["id"])
            if command["state"] not in ("starting", "running"):
                break
            time.sleep(.05)
        assert command["state"] == "completed" and command["exit_code"] == 0, command
        assert "Nexus bundled Python READY" in command["output"], command
        result["bundled_python_console"] = "PASS"
        skills = call("skills.configure", enabled=True)
        assert skills["enabled"] is True and skills["items"] == []
        assert call("skills.list")["enabled"] is True
        result["skills_api"] = "PASS"
        if args.native:
            model = json.loads((bootstrap.ROOT / "runtime/catalog.json").read_text(encoding="utf-8"))["models"][0]
            call("model.start", filename=model["filename"])
            chat = call("chat.begin", prompt="Write one sentence about the sun.", max_tokens=32)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status = call("chat.poll", chat_id=chat["chat_id"])
                if status["state"] != "running":
                    assert status["state"] == "completed" and status["text"].strip(), status
                    result["native_generation"] = "PASS"
                    result["generated_characters"] = len(status["text"])
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("Packaged inference timed out")
            call("model.stop")
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        child.stderr.close()
atomic_json(bootstrap.ROOT / "work/packaged-smoke.json", result)
print(json.dumps(result, indent=2))
