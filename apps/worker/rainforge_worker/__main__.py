from __future__ import annotations
import argparse
import json
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from rainforge_recovery import ProjectLock
from .rpc import Worker, respond

MAX_REQUEST = 128 * 1024


def stdio(worker):
    output_lock = threading.Lock()
    slots = threading.BoundedSemaphore(8)

    def run(message):
        try:
            result = respond(worker, message)
            with output_lock:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            slots.release()

    with ThreadPoolExecutor(max_workers=4) as pool:
        while line := sys.stdin.buffer.readline(MAX_REQUEST + 1):
            if len(line) > MAX_REQUEST:
                raise ValueError("RPC request size exceeded")
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            slots.acquire()
            pool.submit(run, message)


def http_server(worker, assets: Path, port: int):
    token = secrets.token_urlsafe(32)
    assets = assets.resolve()
    slots = threading.BoundedSemaphore(8)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass  # No prompts or headers in access logs.

        def send(self, code, data, content_type="application/json"):
            try:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionError, TimeoutError):
                pass  # A closing view may cancel its pending long-poll response.

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, b'{}')
            path = unquote(urlsplit(self.path).path)
            if path == "/api/bootstrap":
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    return self.send(403, b'{}')
                return self.send(200, json.dumps({"token": token}).encode())
            target = (assets / (path.lstrip("/") or "index.html")).resolve()
            if not target.is_relative_to(assets) or not target.is_file() or target.is_symlink():
                return self.send(404, b'{}')
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
            return self.send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))

        def do_POST(self):
            if not self.valid_host() or self.path != "/api/rpc" or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}" or not secrets.compare_digest(self.headers.get("X-RainForge-Token", ""), token):
                return self.send(403, b'{}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    return self.send(413, b'{}')
                if not slots.acquire(blocking=False):
                    return self.send(429, b'{}')
                try:
                    message = json.loads(self.rfile.read(length))
                    result = respond(worker, message)
                    return self.send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"))
                finally:
                    slots.release()
            except (ValueError, OSError):
                return self.send(400, b'{}')

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"RainForge development UI: http://127.0.0.1:{server.server_port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=8844)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with ProjectLock(args.data_dir / "worker.lock"):
        worker = Worker(args.data_dir, args.runtime_dir, args.models_dir)
        try:
            if args.http:
                if not args.assets:
                    raise ValueError("--assets is required with --http")
                http_server(worker, args.assets, args.port)
            else:
                stdio(worker)
        finally:
            worker.close()


if __name__ == "__main__":
    main()
