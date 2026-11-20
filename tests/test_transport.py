import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap


class TransportTests(unittest.TestCase):
    def test_real_stdio_worker_unicode_and_structured_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            messages = [
                {"jsonrpc": "2.0", "id": "one", "method": "goal.submit", "params": {"goal": "/note Привет из IPC", "request_id": "ipc-proof"}},
                {"jsonrpc": "2.0", "id": "two", "method": "shell.execute", "params": {}},
            ]
            child = subprocess.run([sys.executable, str(bootstrap.ROOT / "scripts/worker.py"), "--data-dir", temp,
                                    "--runtime-dir", str(bootstrap.ROOT / "runtime")],
                                   input="\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n",
                                   capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(child.returncode, 0, child.stderr)
            responses = {r["id"]: r for r in map(json.loads, child.stdout.splitlines())}
            self.assertEqual(responses["one"]["result"]["status"], "COMPLETED")
            self.assertIn("Привет", responses["one"]["result"]["goal"])
            self.assertIn("error", responses["two"])
            self.assertEqual(len(list((Path(temp) / "workspace/artifacts").glob("*.md"))), 1)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("RF_NATIVE_TEST") == "1", "Requires Windows and installed native runtime")
    def test_worker_crash_terminates_owned_llama_process(self):
        import ctypes
        from ctypes import wintypes

        code = """import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import bootstrap
from rainforge_native import NativeRuntime
r=NativeRuntime(bootstrap.ROOT/'runtime')
m=json.loads((bootstrap.ROOT/'runtime/catalog.json').read_text(encoding='utf-8'))['models'][0]
r.start(m['filename'])
print(r.process.pid,flush=True)
input()
"""
        child = subprocess.Popen([sys.executable, "-c", code, str(bootstrap.ROOT / "scripts")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        try:
            line = child.stdout.readline().strip()
            self.assertTrue(line.isdigit(), line or child.stderr.read())
            handle = api.OpenProcess(0x00100000, False, int(line))  # SYNCHRONIZE
            self.assertTrue(handle)
            child.kill()
            child.wait(timeout=5)
            self.assertEqual(api.WaitForSingleObject(handle, 5000), 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if handle:
                api.CloseHandle(handle)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()
