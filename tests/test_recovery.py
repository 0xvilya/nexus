import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap
from rainforge_recovery import ProjectLock, LockBusy, atomic_json


class RecoveryTests(unittest.TestCase):
    def test_atomic_replace_and_failed_serialization_preserves_old_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "STATE.json"
            atomic_json(path, {"version": 1})
            with self.assertRaises(TypeError):
                atomic_json(path, {"bad": object()})
            self.assertEqual(json.loads(path.read_text()), {"version": 1})
            self.assertEqual([p.name for p in Path(temp).iterdir()], ["STATE.json"])
            atomic_json(path, {"version": 2, "text": "Привет"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 2)

    def test_competing_process_lock_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "development.lock"
            code = "import sys; sys.path.insert(0, sys.argv[1]); import bootstrap; from rainforge_recovery import ProjectLock; lock=ProjectLock(__import__('pathlib').Path(sys.argv[2])); lock.__enter__(); print('ready:'+str(__import__('os').getpid()),flush=True); input()"
            child = subprocess.Popen([sys.executable, "-c", code, str(bootstrap.ROOT / "scripts"), str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                ready=child.stdout.readline().strip()
                self.assertTrue(ready.startswith("ready:"))
                owner_pid=int(ready.split(":")[1])
                with self.assertRaises(LockBusy):
                    with ProjectLock(lock):
                        self.fail("Competing lock acquired")
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
                else:child.kill()
                child.wait(timeout=5)
                with ProjectLock(lock) as acquired:
                    self.assertEqual(acquired.recovered["pid"], owner_pid)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdin.close()
                child.stdout.close()


if __name__ == "__main__":
    unittest.main()
