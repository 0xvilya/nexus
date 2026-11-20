"""Source-tree imports; packaged worker collects these packages explicitly."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "packages").iterdir() if (ROOT / "packages").is_dir() else ():
    if path.is_dir():
        sys.path.insert(0, str(path))
sys.path.insert(0, str(ROOT / "apps" / "worker"))
