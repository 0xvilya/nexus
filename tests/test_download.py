import hashlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap
from rainforge_native.download import download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "model.gguf"
        self.data = b"real resumable bytes" * 1000
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.mode = "range"
        self.ranges = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
                case.ranges.append(start)
                if case.mode == "temporary-error" and len(case.ranges) == 1:
                    self.send_error(503)
                    return
                if case.mode == "restart":
                    self.send_response(200)
                    payload = case.data
                else:
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {0 if case.mode == 'wrong-range' else start}-{len(case.data)-1}/{len(case.data)}")
                    payload = case.data[start:]
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if case.mode == "truncate" and len(case.ranges) == 1:
                    payload = payload[:177]
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}/file"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_resume_byte_range_and_hash(self):
        self.target.with_suffix(".gguf.part").write_bytes(self.data[:777])
        download(self.url, self.target, self.digest, len(self.data))
        self.assertEqual(self.ranges, [777])
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(self.target.with_suffix(".gguf.part").exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        self.mode = "restart"
        self.target.with_suffix(".gguf.part").write_bytes(self.data[:777])
        download(self.url, self.target, self.digest, len(self.data))
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_wrong_range_never_registers_model(self):
        self.mode = "wrong-range"
        self.target.with_suffix(".gguf.part").write_bytes(self.data[:777])
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            download(self.url, self.target, self.digest, len(self.data))
        self.assertFalse(self.target.exists())

    def test_wrong_hash_never_registers_model(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            download(self.url, self.target, "0" * 64, len(self.data))
        self.assertFalse(self.target.exists())

    def test_disconnected_transfer_resumes_automatically(self):
        self.mode = "truncate"
        download(self.url, self.target, self.digest, len(self.data))
        self.assertEqual(self.ranges, [0, 177])
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_temporary_server_error_retries_same_offset(self):
        self.mode = "temporary-error"
        self.target.with_suffix(".gguf.part").write_bytes(self.data[:777])
        download(self.url, self.target, self.digest, len(self.data))
        self.assertEqual(self.ranges, [777, 777])
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_bad_cached_partial_recovers_with_clean_transfer(self):
        part = self.target.with_suffix(".gguf.part")
        for invalid in (b"x" * 777, b"x" * len(self.data), b"x" * (len(self.data) + 1)):
            with self.subTest(length=len(invalid)):
                self.target.unlink(missing_ok=True)
                self.ranges.clear()
                part.write_bytes(invalid)
                download(self.url, self.target, self.digest, len(self.data))
                self.assertEqual(self.ranges, [len(invalid), 0] if len(invalid) < len(self.data) else [0])
                self.assertEqual(self.target.read_bytes(), self.data)
