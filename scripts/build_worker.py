"""Build a Python-independent stdio sidecar; requires PyInstaller in build venv."""
import subprocess
import sys

import bootstrap

args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "rainforge-worker-x86_64-pc-windows-msvc",
        "--distpath", str(bootstrap.ROOT / "apps/desktop/src-tauri/binaries"),
        "--workpath", str(bootstrap.ROOT / "work/pyinstaller"),
        "--specpath", str(bootstrap.ROOT / "work")]
for path in [bootstrap.ROOT / "scripts", bootstrap.ROOT / "apps/worker", *(bootstrap.ROOT / "packages").iterdir()]:
    if path.is_dir():
        args.extend(["--paths", str(path)])
args.append(str(bootstrap.ROOT / "scripts/worker.py"))
subprocess.run(args, cwd=bootstrap.ROOT, check=True)
