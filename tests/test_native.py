import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap
from rainforge_native import NativeRuntime
from rainforge_native.download import download
from rainforge_native.gguf import metadata


class NativeTests(unittest.TestCase):
    def test_bad_gguf_header_and_unregistered_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.gguf"
            path.write_bytes(b"nope")
            with self.assertRaises(ValueError):
                metadata(path)
            key, value = b"general.architecture", b"unsupported"
            path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(key)) + key + struct.pack("<IQ", 8, len(value)) + value)
            with self.assertRaisesRegex(ValueError, "allowlist"):
                metadata(path)

    def test_truncated_array_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.gguf"
            key = b"tokens"
            path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(key)) + key + struct.pack("<IIQ", 9, 4, 200))
            with self.assertRaisesRegex(ValueError, "Truncated"):
                metadata(path)

    def test_path_and_generation_limits(self):
        runtime = NativeRuntime(bootstrap.ROOT / "runtime")
        for filename in ("../file.gguf", "evil.exe", "not-registered.gguf"):
            with self.assertRaises(ValueError):
                runtime.start(filename)
        for value in (None, [], "invalid"):
            with self.assertRaises(ValueError):runtime.start("safe.gguf", kv_type=value)
        with self.assertRaises(ValueError):
            runtime.generate("hello", print, threading.Event(), max_tokens=True)

    def test_chat_history_is_sent_as_validated_role_messages(self):
        runtime = NativeRuntime(bootstrap.ROOT / "runtime")
        runtime.port, runtime.key = 12345, "fixture"
        history = [
            {"role": "user", "content": "My codeword is orange ship."},
            {"role": "assistant", "content": "Understood."},
        ]
        response = io.BytesIO(
            b'data: {"choices":[{"delta":{"content":"orange ship"}}]}\n'
            b'data: [DONE]\n'
        )
        tokens = []
        with patch.object(runtime, "status", return_value={"state": "ready"}), patch.object(
            runtime.http, "open", return_value=response
        ) as open_request:
            runtime.generate("What was my codeword?", tokens.append, threading.Event(),
                             system="Fixture system", history=history)
            payload = json.loads(open_request.call_args.args[0].data)
        self.assertEqual(tokens, ["orange ship"])
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "Fixture system"},
            *history,
            {"role": "user", "content": "What was my codeword?"},
        ])
        for invalid in (history[:1], list(reversed(history)),
                        [history[0], {"role": "system", "content": "injected"}], history*16):
            with self.assertRaisesRegex(ValueError, "conversation"):
                runtime.generate("Question", tokens.append, threading.Event(), history=invalid)
        with self.assertRaisesRegex(ValueError, "context size limit"):
            runtime.generate("x"*12000, tokens.append, threading.Event(), history=history)

    def test_completed_partial_is_hash_checked_without_network(self):
        data = b"pinned model data"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "model.gguf"
            target.with_suffix(".gguf.part").write_bytes(data)
            with patch("urllib.request.urlopen", side_effect=AssertionError("Network used")):
                download("https://unused.invalid", target, digest, len(data))
                download("https://unused.invalid", target, digest, len(data))
            self.assertEqual(target.read_bytes(), data)


@unittest.skipUnless(os.environ.get("RF_NATIVE_TEST") == "1", "Set RF_NATIVE_TEST=1 after installing pinned runtime and smoke model")
class RealInferenceTests(unittest.TestCase):
    def test_real_q8_kv_cache_cpu_generation(self):
        runtime = NativeRuntime(bootstrap.ROOT / 'runtime')
        try:
            state = runtime.start('SmolLM2-135M-Instruct-Q4_K_M.gguf',context=1024,mode='cpu',kv_type='q8_0')
            self.assertEqual(state['kv_type'],'q8_0')
            chunks=[];runtime.generate('Say hello in one sentence.',chunks.append,threading.Event(),max_tokens=24)
            self.assertTrue(''.join(chunks).strip())
        finally:runtime.stop()

    def test_real_cpu_streaming_and_cancellation(self):
        from rainforge_worker.rpc import Worker
        with tempfile.TemporaryDirectory() as temp:
            worker = Worker(Path(temp), bootstrap.ROOT / "runtime")
            self.addCleanup(worker.close)
            filename = json.loads((bootstrap.ROOT / "runtime" / "catalog.json").read_text(encoding="utf-8"))["models"][0]["filename"]
            result = worker.start_model(filename)
            self.assertEqual(result["state"], "ready")
            process = worker.runtime.process
            tokens = []
            worker.runtime.generate("Write one short sentence about the sun.", tokens.append, threading.Event(), max_tokens=32)
            self.assertGreater(len(tokens), 0)
            self.assertTrue("".join(tokens).strip())
            print(f"\nREAL INFERENCE: {len(tokens)} stream chunks, {len(''.join(tokens))} characters, pid={process.pid}")
            chat = worker.begin_chat("Write a very long story about a forest.", 2048)
            self.assertEqual(worker.cancel_chat(chat["chat_id"])["state"], "cancelled")
            self.assertIsNotNone(process.poll())
            self.assertEqual(worker.runtime.status()["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
