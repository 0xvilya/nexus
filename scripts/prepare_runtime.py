"""Explicit developer installation of pinned, hash-verified native components."""
import argparse
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import bootstrap
from rainforge_native.download import download, sha256
from rainforge_recovery import atomic_json, ProjectLock


def install(root: Path, accept_license: bool, include_model: bool = False, progress=print):
    if not accept_license:
        raise ValueError("Read runtime/catalog.json and pass --accept-license to accept MIT / Apache-2.0")
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    with ProjectLock(root / "install.lock"):
        cache = root / "cache"
        info = catalog["runtime"]
        archive = download(info["url"], cache / f"llama-{info['version']}.zip", info["sha256"], info["bytes"])
        progress("Runtime archive integrity verified")
        staging = Path(tempfile.mkdtemp(prefix="runtime-", dir=root))
        try:
            with zipfile.ZipFile(archive) as source:
                for entry in source.infolist():
                    name = Path(entry.filename).name
                    if not name or entry.is_dir():
                        continue
                    if name.endswith((".exe", ".dll")):
                        if entry.file_size > 200 * 1024 * 1024:
                            raise ValueError("Unexpected runtime entry size")
                        with source.open(entry) as src, (staging / name).open("xb") as dst:
                            shutil.copyfileobj(src, dst)
            # Keep only the runtime executable and its native dependencies.
            for path in staging.glob("*.exe"):
                if path.name != "llama-server.exe":
                    path.unlink()
            if not (staging / "llama-server.exe").exists():
                raise ValueError("Archive has no llama-server.exe")
            files = {p.name: sha256(p) for p in staging.iterdir()}
            license_url = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{info['version']}/LICENSE"
            with urllib.request.urlopen(license_url, timeout=30) as response:
                (staging / "LICENSE-llama.cpp.txt").write_bytes(response.read(100000))
            atomic_json(staging / "manifest.json", {"version": info["version"], "archive_sha256": info["sha256"], "files": files})
            destination = root / "bin"
            if destination.exists():
                # Do not alter a possibly running runtime or delete the installed copy.
                if json.loads((destination / "manifest.json").read_text())["archive_sha256"] != info["sha256"]:
                    raise ValueError("Runtime update requires an explicit migration")
            else:
                staging.replace(destination)
            if include_model:
                model = catalog["models"][0]
                progress(f"Downloading {model['name']} ({model['bytes']} bytes)")
                download(model["url"], root / "models" / model["filename"], model["sha256"], model["bytes"])
                progress("Model integrity verified")
        finally:
            if staging.exists():
                shutil.rmtree(staging)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--with-smoke-model", action="store_true")
    args = parser.parse_args()
    install(bootstrap.ROOT / "runtime", args.accept_license, args.with_smoke_model)
