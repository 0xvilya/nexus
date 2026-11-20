from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class LockBusy(RuntimeError):
    pass


class ProjectLock:
    """The OS lock, never a PID/age heuristic, decides ownership.

    The lock inode is never unlinked. A crash releases the OS lock; leftover
    metadata is recorded as stale only after exclusive acquisition succeeds.
    """

    def __init__(self, path: Path):
        self.path = path
        self.stream = None
        self.token = uuid4().hex
        self.recovered = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise LockBusy(f"An active process owns {self.path}") from exc
        self.stream = stream
        meta = self.path.with_suffix(self.path.suffix + ".json")
        try:
            try:
                previous = json.loads(meta.read_text(encoding="utf-8"))
                if previous.get("status") == "active":
                    self.recovered = previous
            except (OSError, ValueError):
                pass
            atomic_json(meta, {"pid": os.getpid(), "token": self.token,
                              "status": "active", "started_at": datetime.now(timezone.utc).isoformat(),
                              "recovered_stale_owner": self.recovered})
        except BaseException:
            self._unlock()
            raise
        return self

    def _unlock(self):
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None

    def __exit__(self, *_):
        try:
            atomic_json(self.path.with_suffix(self.path.suffix + ".json"),
                        {"pid": os.getpid(), "token": self.token, "status": "released"})
        finally:
            self._unlock()
