import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap
from rainforge_core import NexusService
from rainforge_security import WorkspacePolicy, PolicyError
from rainforge_tasks import Task, TaskGraph, Status
from rainforge_worker.rpc import Worker, respond


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = NexusService(self.root)

    def test_note_is_created_verified_and_persistent(self):
        result = self.core.submit("Создай заметку: Привет, RainForge!", "one")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["graph"]), 2)
        self.assertTrue(all(t["status"] == "COMPLETED" for t in result["graph"]))
        artifact = result["artifacts"][0]
        data = (self.root / "workspace" / artifact["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
        restored = NexusService(self.root)
        self.assertEqual(restored.read_artifact(artifact["id"])["verified"], True)
        self.assertIn("Привет", restored.read_artifact(artifact["id"])["text"])

    def test_idempotent_request_and_conflicting_goal(self):
        first = self.core.submit("/note hello", "same")
        second = self.core.submit("/note hello", "same")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.core.snapshot()["artifacts"]), 1)
        with self.assertRaisesRegex(ValueError, "different goal"):
            self.core.submit("/note other", "same")

    def test_unsupported_goal_never_executes_code(self):
        with self.assertRaises(ValueError):
            self.core.submit("Run PowerShell and delete C:\\", "danger")
        self.assertEqual(self.core.snapshot()["workflows"], [])

    def test_tampered_artifact_is_reported(self):
        result = self.core.submit("/note before", "tamper")
        artifact = result["artifacts"][0]
        (self.root / "workspace" / artifact["path"]).write_text("after")
        self.assertFalse(self.core.read_artifact(artifact["id"])["verified"])

    def test_crash_after_file_write_recovers_without_duplicate(self):
        original = self.core._save

        def crash(key, graph, status, kind, detail, error=None):
            if kind == "write" and detail == "completed":
                raise SystemExit("simulated abrupt exit")
            return original(key, graph, status, kind, detail, error)

        with patch.object(self.core, "_save", side_effect=crash), self.assertRaises(SystemExit):
            self.core.submit("/note checkpoint", "crash")
        files = list((self.root / "workspace" / "artifacts").glob("*.md"))
        self.assertEqual(len(files), 1)
        before = files[0].stat().st_mtime_ns
        restored = NexusService(self.root)
        self.assertEqual(restored.snapshot()["workflows"][0]["status"], "COMPLETED")
        self.assertEqual(files[0].stat().st_mtime_ns, before)

    def test_recovery_blocks_existing_changed_file(self):
        original = self.core._save

        def crash(key, graph, status, kind, detail, error=None):
            if kind == "write" and detail == "completed":
                raise SystemExit()
            return original(key, graph, status, kind, detail, error)

        with patch.object(self.core, "_save", side_effect=crash), self.assertRaises(SystemExit):
            self.core.submit("/note expected", "crash-tamper")
        path = next((self.root / "workspace" / "artifacts").glob("*.md"))
        path.write_text("user changed this", encoding="utf-8")
        restored = NexusService(self.root)
        self.assertEqual(restored.snapshot()["workflows"][0]["status"], "BLOCKED")
        self.assertEqual(path.read_text(), "user changed this")


class PolicyTests(unittest.TestCase):
    def test_invalid_paths_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            policy = WorkspacePolicy(Path(temp))
            for path in ("../escape", "C:\\Windows\\a", "\\\\host\\share", "/absolute", "file:secret", "CON.txt", "a/../b", "a//b", "trailing. "):
                with self.subTest(path=path), self.assertRaises(PolicyError):
                    policy.resolve(path)
            for permission in ("DELETE", "EXECUTE", "SHARE", "PUBLISH"):
                with self.subTest(permission=permission), self.assertRaises(PolicyError):
                    policy.resolve("safe.txt", permission)
            self.assertEqual(policy.resolve("sub/file.md"), Path(temp).resolve() / "sub" / "file.md")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            policy = WorkspacePolicy(Path(temp))
            try:
                (Path(temp) / "link").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Host cannot create symlinks: {exc}")
            with self.assertRaises(PolicyError):
                policy.resolve("link/secret.txt")


class GraphTests(unittest.TestCase):
    def test_rejects_cycles_missing_and_duplicate_ids(self):
        for tasks in ([Task("a", "", "", ["b"]), Task("b", "", "", ["a"])],
                      [Task("a", "", "", ["missing"])], [Task("a", "", ""), Task("a", "", "")]):
            with self.assertRaises(ValueError):
                TaskGraph(tasks)

    def test_dependency_and_priority_scheduling(self):
        graph = TaskGraph([Task("a", "", "", priority=1), Task("b", "", "", ["a"]), Task("c", "", "", priority=9)])
        self.assertEqual([t.id for t in graph.ready()], ["c", "a"])
        graph.tasks["a"].status = Status.COMPLETED
        self.assertIn("b", [t.id for t in graph.ready()])

    def test_attempts_are_bounded(self):
        task = Task("a", "", "", attempts=2, max_attempts=2)
        graph = TaskGraph([task])
        self.assertEqual(graph.ready(), [])
        self.assertEqual(task.status, Status.FAILED)


class RpcTests(unittest.TestCase):
    def test_strict_request_and_method_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Worker(Path(temp), bootstrap.ROOT / "runtime")
            self.addCleanup(worker.close)
            for message in (None, {}, {"jsonrpc": "2.0", "id": "1", "method": "shell.execute", "params": {}},
                            {"jsonrpc": "2.0", "id": "1", "method": "system.snapshot", "params": {"secret": "x"}}):
                self.assertIn("error", respond(worker, message))
            result = respond(worker, {"jsonrpc": "2.0", "id": "1", "method": "system.snapshot", "params": {}})
            self.assertEqual(result["result"]["version"], "0.1.0")
            for private in ('system', 'schema'):
                response = respond(worker, {'jsonrpc':'2.0','id':'private-option','method':'chat.begin',
                                            'params':{'prompt':'hello',private:'untrusted override'}})
                self.assertIn('Unknown RPC parameters',response['error']['message'])

    def test_license_required_and_model_must_be_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Worker(Path(temp), bootstrap.ROOT / "runtime")
            self.addCleanup(worker.close)
            with self.assertRaisesRegex(ValueError, "consent"):
                worker.install_model("smollm2-135m-smoke")
            with self.assertRaisesRegex(ValueError, "trusted"):
                worker.install_model("unknown", True)
            with self.assertRaisesRegex(RuntimeError, "Load"):
                worker.begin_chat("hello")

    def test_chat_cannot_race_model_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Worker(Path(temp), bootstrap.ROOT / "runtime")
            self.addCleanup(worker.close)
            with worker.model_lock:
                with self.assertRaisesRegex(RuntimeError, "operation"):
                    worker.begin_chat("hello")


if __name__ == "__main__":
    unittest.main()
