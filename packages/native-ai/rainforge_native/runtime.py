from __future__ import annotations
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .download import sha256
from .gguf import metadata
from .windows_job import WindowsJob


class NativeRuntime:
    def __init__(self, root: Path, models_dir: Path | None = None):
        self.root = root
        self.models_dir = models_dir if models_dir is not None else root / "models"
        self.process = None
        self.job = None
        self.port = None
        self.key = None
        self.model = None
        self.mode = "cpu"
        self.context = 2048
        self.kv_type = "f16"
        self.lifecycle = threading.RLock()
        self.generation = threading.Lock()
        # Never send localhost requests through a system HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def status(self):
        alive = self.process is not None and self.process.poll() is None
        return {"state": "ready" if alive and self.model else "stopped", "model": self.model if alive else None,
                "backend": self.mode, "context": self.context if alive else None, "kv_type": self.kv_type if alive else None, "runtime_installed": (self.root / "bin" / "llama-server.exe").exists()}

    def model_path(self, filename: str) -> Path:
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".gguf"):
            raise ValueError("Expected a registered GGUF filename")
        folder = self.models_dir
        path = folder / filename
        # Keep user models in their existing directory. The installer owns only
        # the bundled starter, and it is validated against the same catalog.
        if filename == "SmolLM2-135M-Instruct-Q4_K_M.gguf" and not path.exists() and not path.is_symlink():
            bundled = self.root / "starter" / filename
            if bundled.is_file():
                folder = self.root / "starter"
                path = bundled
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Unsafe model path")
        return path

    def has_model(self, filename: str) -> bool:
        try:
            return self.model_path(filename).is_file()
        except (ValueError, OSError):
            return False

    def start(self, filename: str, context: int = 2048, mode: str = "cpu", gpu_layers: int = 12, kv_type: str = "f16"):
        with self.lifecycle:
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".gguf"):
                raise ValueError("Expected a registered GGUF filename")
            if type(context) is not int or not 512 <= context <= 8192:
                raise ValueError("Context must be an integer between 512 and 8192")
            if not isinstance(kv_type, str) or kv_type not in {'f16', 'q8_0'}:
                raise ValueError('Unsupported KV cache type')
            if mode not in {'cpu','gpu','hybrid','auto'} or type(gpu_layers) is not int or not 1 <= gpu_layers <= 200:
                raise ValueError('Invalid inference mode/layer count')
            if mode == 'auto': mode = 'hybrid' if (self.root / 'vulkan/manifest.json').exists() else 'cpu'
            bin_dir = self.root / ('bin' if mode == 'cpu' else 'vulkan')
            if not (bin_dir / 'manifest.json').exists():
                raise ValueError('GPU runtime not installed. Use CPU mode.')
            catalog = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
            custom = self.models_dir / 'custom.json'
            if custom.exists(): catalog['models'] += json.loads(custom.read_text(encoding='utf-8'))
            model = next((m for m in catalog["models"] if m["filename"] == filename), None)
            if not model:
                raise ValueError("Model is not registered in the verified catalog")
            path = self.model_path(filename)
            if path.stat().st_size != model["bytes"] or sha256(path) != model["sha256"]:
                raise ValueError("Model integrity check failed")
            metadata(path)
            manifest = json.loads((bin_dir / "manifest.json").read_text(encoding="utf-8"))
            if manifest["archive_sha256"] != (catalog["runtime"] if mode == "cpu" else catalog["backends"]["vulkan"])["sha256"]:
                raise ValueError("Runtime version does not match catalog")
            native_files = {p.name for p in (bin_dir).iterdir() if p.suffix.lower() in {".exe", ".dll"}}
            if native_files != set(manifest["files"]):
                raise ValueError("Runtime contains unregistered executable components")
            for name, digest in manifest["files"].items():
                candidate = bin_dir / name
                if Path(name).name != name or candidate.is_symlink() or sha256(candidate) != digest:
                    raise ValueError("Runtime integrity check failed")
            if "llama-server.exe" not in manifest["files"]:
                raise ValueError("Runtime manifest is incomplete")
            projector = model.get('projector')
            if projector:
                projector_path = self.models_dir / projector['filename']
                if sha256(projector_path) != projector['sha256']: raise ValueError('Vision projector integrity check failed')
            self.stop()
            self.mode = mode
            self.context = context
            self.kv_type = kv_type
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self.port = sock.getsockname()[1]
            self.key = secrets.token_urlsafe(32)
            env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
            env["LLAMA_API_KEY"] = self.key
            args = [str(bin_dir / "llama-server.exe"), "--model", str(path),
                    "--host", "127.0.0.1", "--port", str(self.port), "--ctx-size", str(context),
                    "--parallel", "1", "--threads", str(max(1, min(4, (os.cpu_count() or 2) - 1))),
                    "--n-gpu-layers", str(0 if mode == 'cpu' else 999 if mode == 'gpu' else gpu_layers), "--no-webui", "--cache-type-k", kv_type, "--cache-type-v", kv_type]
            if projector: args += ['--mmproj',str(projector_path)]
            self.job = WindowsJob()
            try:
                self.process = subprocess.Popen(args, cwd=bin_dir, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.job.assign(self.process)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError(f"llama.cpp exited during startup (code {self.process.returncode})")
                    try:
                        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/models", headers={"Authorization": f"Bearer {self.key}"})
                        with self.http.open(request, timeout=1) as response:
                            if response.status == 200:
                                self.model = filename
                                return self.status()
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.1)
                raise TimeoutError("Model startup exceeded 60 seconds")
            except BaseException:
                self.stop()
                raise

    def stop(self):
        with self.lifecycle:
            if self.process:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                self.process = None
            if self.job:
                self.job.close()
                self.job = None
            self.model = None
            self.key = None
            return self.status()

    def generate(self, prompt: str, on_token, cancelled: threading.Event, max_tokens: int = 256, image: str | None = None, system: str | None = None, schema: dict | None = None, history: list[dict] | None = None, repeat_penalty: float | None = None):
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 12000:
            raise ValueError("Prompt must contain 1-12000 characters")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
            raise ValueError("Token budget must be an integer between 1 and 2048")
        if system is not None and (not isinstance(system,str) or not 1<=len(system)<=3000):
            raise ValueError('Invalid internal system instructions')
        if schema is not None and (not isinstance(schema,dict) or len(json.dumps(schema))>16000):
            raise ValueError('Invalid internal response schema')
        if history is not None:
            if not isinstance(history, list) or len(history) > 30 or len(history) % 2:
                raise ValueError('Invalid internal conversation history')
            size = len(prompt)
            for index, message in enumerate(history):
                role = 'assistant' if index % 2 else 'user'
                if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                        or message['role'] != role or not isinstance(message['content'], str)
                        or len(message['content']) > 2000):
                    raise ValueError('Invalid internal conversation message')
                size += len(message['content'])
            if size > 12000:
                raise ValueError('Conversation context size limit exceeded')
        if not self.generation.acquire(blocking=False):
            raise RuntimeError("A generation is already running")
        try:
            with self.lifecycle:
                if self.status()["state"] != "ready":
                    raise RuntimeError("Install and load a local model first")
                port, key = self.port, self.key
            content = prompt
            if image:
                if not image.startswith('data:image/jpeg;base64,') or len(image)>90000: raise ValueError('Invalid image; use a JPEG under 65 KB')
                content = [{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':image}}]
            messages = ([{"role": "system", "content": system}] if system else [])
            messages.extend(history or [])
            messages.append({"role": "user", "content": content})
            payload={"messages": messages, "stream": True,"max_tokens": max_tokens, "temperature": 0.2}
            if repeat_penalty is not None:payload['repeat_penalty']=repeat_penalty
            if schema is not None:payload['response_format']={'type':'json_object','schema':schema}
            data = json.dumps(payload).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=data,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with self.http.open(request, timeout=60) as response:
                while not cancelled.is_set():
                    line = response.readline(1024 * 1024)
                    if not line:
                        raise RuntimeError("Inference stream closed before completion")
                    if not line.startswith(b"data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == b"[DONE]":
                        return
                    event = json.loads(payload)
                    if "error" in event:
                        raise RuntimeError("Inference returned an error")
                    choices = event.get("choices", [])
                    if choices:
                        token = choices[0].get("delta", {}).get("content")
                        if token:
                            on_token(token)
        finally:
            self.generation.release()
