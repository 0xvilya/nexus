"""Read-only orientation before taking the development lock."""
import json
import subprocess

import bootstrap

for name in ("STATE.json", "QUEUE.json"):
    path = bootstrap.ROOT / "automation" / name
    print(f"{name}:\n{json.dumps(json.loads(path.read_text(encoding='utf-8')), ensure_ascii=False, indent=2)}")
subprocess.run(["git", "status", "--short"], cwd=bootstrap.ROOT, check=True)
subprocess.run(["git", "log", "-1", "--oneline"], cwd=bootstrap.ROOT, check=True)
print("Read AGENTS.md, then hold scripts/session.py open before any modification.")
