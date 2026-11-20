from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import unicodedata
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4

from rainforge_recovery import atomic_json

MAX_SOURCE = 1024 ** 3
MAX_RECORD = 1024 ** 2
MAX_ROWS = 1_000_000
MAX_OUTPUT = 2 * 1024 ** 3
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
FILES = ("train.jsonl", "validation.jsonl", "test.jsonl", "dataset-card.md")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def normalize(value, depth=0):
    if depth > 24:
        raise ValueError("JSON nesting exceeds 24 levels")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    if isinstance(value, dict):
        if len(value) > 256:
            raise ValueError("Record exceeds 256 fields")
        return {key: normalize(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item, depth + 1) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON numbers are unsupported")
    return value


def empty(value):
    if isinstance(value, (dict, list)):
        return all(empty(item) for item in (value.values() if isinstance(value, dict) else value))
    return value is None or value == ""


def object_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class DatasetEngine:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / "datasets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.cancelled = threading.Event()
        self.thread = None
        self.upload = None
        # Never repeat interrupted work or publish its partial outputs automatically.
        for path in self.root.glob("*/state.json"):
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["status"] in ("RUNNING", "VERIFYING"):
                state.update(status="INTERRUPTED", error="Worker stopped; choose the source again to retry")
                atomic_json(path, state)

    def listing(self):
        with self.guard:
            runs = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*/state.json")]
            return {"runs": sorted(runs, key=lambda row: row["created_ns"], reverse=True)[:100],
                    "root": str(self.root.resolve()), "active": bool(self.thread and self.thread.is_alive())}

    def _save(self, folder, state):
        # Windows readers can temporarily deny rename/delete sharing. Serialize our
        # history reads and progress commits rather than failing a valid pipeline.
        with self.guard:
            atomic_json(folder / "state.json", state)

    def upload_begin(self, name, size):
        if not isinstance(name, str) or Path(name).suffix.lower() not in (".csv", ".jsonl"):
            raise ValueError("Choose a CSV or JSONL file")
        if type(size) is not int or not 1 <= size <= 64 * 1024 ** 2:
            raise ValueError("File picker supports 1 byte–64 MiB; use a local path for larger files")
        with self.guard:
            if self.upload:
                raise ValueError("An upload is already active; cancel it first")
            if shutil.disk_usage(self.root).free < size + 64 * 1024 ** 2:
                raise ValueError("Insufficient disk space")
            key = uuid4().hex
            folder = self.root / "inputs"
            folder.mkdir(exist_ok=True)
            path = folder / (key + Path(name).suffix.lower() + ".part")
            path.touch(exist_ok=False)
            self.upload = {"id": key, "path": path, "size": size, "received": 0}
            return {"upload_id": key}

    def upload_chunk(self, upload_id, offset, data):
        if type(offset) is not int or not isinstance(data, str) or not 1 <= len(data) <= 45000:
            raise ValueError("Invalid upload chunk")
        block = base64.b64decode(data, validate=True)
        with self.guard:
            current = self.upload
            if not current or current["id"] != upload_id or offset != current["received"]:
                raise ValueError("Upload offset does not match; no bytes written")
            if not block or offset + len(block) > current["size"]:
                raise ValueError("Upload exceeds declared size")
            with current["path"].open("ab") as stream:
                stream.write(block)
            current["received"] += len(block)
            return {"received": current["received"]}

    def upload_finish(self, upload_id):
        with self.guard:
            current = self.upload
            if not current or current["id"] != upload_id or current["received"] != current["size"]:
                raise ValueError("Upload is incomplete")
            final = current["path"].with_suffix("")
            os.replace(current["path"], final)
            self.upload = None
            return {"source": str(final.resolve()), "sha256": digest(final)}

    def upload_cancel(self):
        with self.guard:
            if self.upload:
                self.upload["path"].unlink(missing_ok=True)
                self.upload = None
            return {"cancelled": True}

    def start(self, source, license, provenance="", exclude_email=False):
        if not isinstance(source, str) or not Path(source).is_absolute() or source.startswith(("\\\\", "//")):
            raise ValueError("Choose an explicit absolute local file path")
        if not isinstance(license, str) or not 1 <= len(license.strip()) <= 200:
            raise ValueError("Record the source license, or 'unknown' when it is unknown")
        if not isinstance(provenance, str) or len(provenance) > 2000 or type(exclude_email) is not bool:
            raise ValueError("Invalid dataset options")
        path = Path(source)
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in (".csv", ".jsonl"):
            raise ValueError("Source must be a regular CSV or JSONL file")
        path = path.resolve()
        size = path.stat().st_size
        if not 1 <= size <= MAX_SOURCE:
            raise ValueError("Source must contain 1 byte–1 GiB")
        if shutil.disk_usage(self.root).free < size * 3 + 64 * 1024 ** 2:
            raise ValueError("Insufficient disk space for outputs and disk-backed deduplication")
        with self.guard:
            if self.thread and self.thread.is_alive():
                raise ValueError("A dataset pipeline is already running")
            self.cancelled.clear()
            key = uuid4().hex
            folder = self.root / key
            folder.mkdir()
            state = {"id": key, "created_ns": __import__("time").time_ns(), "status": "RUNNING",
                     "source": str(path), "source_bytes": size, "license": license.strip(),
                     "provenance": provenance, "output_dir": str(folder.resolve()), "error": None,
                     "recipe": {"version": 1, "normalize": "NFC and trim string values", "deduplicate": "canonical JSON SHA-256",
                                "split": "SHA-256 modulo 100: 80/10/10", "exclude_email": exclude_email},
                     "stats": {"read": 0, "empty": 0, "duplicates": 0, "email_flagged": 0, "email_excluded": 0,
                               "kept": 0, "train": 0, "validation": 0, "test": 0}, "files": []}
            self._save(folder, state)
            self.thread = threading.Thread(target=self._run, args=(path, folder, state), daemon=True)
            self.thread.start()
            return {"id": key}

    def cancel(self):
        self.cancelled.set()
        return {"requested": True}

    def _check(self):
        if self.cancelled.is_set():
            raise InterruptedError("Dataset processing cancelled; partial files are not verified")

    def _rows(self, path, hasher):
        total, record_bytes = 0, 0
        with path.open("rb") as stream:
            def lines():
                nonlocal total, record_bytes
                while True:
                    self._check()
                    line = stream.readline(MAX_RECORD + 1)
                    if not line:
                        break
                    total += len(line)
                    record_bytes += len(line)
                    if total > MAX_SOURCE or record_bytes > MAX_RECORD:
                        raise ValueError("Source or record size limit exceeded (1 GiB / 1 MiB)")
                    hasher.update(line)
                    yield line.decode("utf-8-sig" if total == len(line) else "utf-8")
            if path.suffix.lower() == ".jsonl":
                for line in lines():
                    record_bytes = 0
                    if not line.strip():
                        continue
                    row = json.loads(line, object_pairs_hook=object_pairs)
                    if not isinstance(row, dict):
                        raise ValueError("Each JSONL record must be an object")
                    yield row
            else:
                # csv has an additional process-wide field limit; it is never raised by untrusted input.
                reader = csv.reader(lines(), strict=True)
                headers = next(reader, None)
                record_bytes = 0
                if not headers or len(headers) > 256 or any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
                    raise ValueError("CSV must have 1–256 unique, nonempty column names")
                for row in reader:
                    record_bytes = 0
                    if not row:
                        continue
                    if len(row) != len(headers):
                        raise ValueError("CSV row does not match header width")
                    yield dict(zip(headers, row))
        self._check()

    def _run(self, source, folder, state):
        stats = state["stats"]
        try:
            before = source.stat()
            hasher = hashlib.sha256()
            with ExitStack() as stack:
                db = stack.enter_context(closing(sqlite3.connect(folder / "dedup.sqlite")))
                db.execute("PRAGMA cache_size=-2048")
                db.execute("PRAGMA journal_mode=OFF")
                db.execute("CREATE TABLE seen (hash TEXT PRIMARY KEY) WITHOUT ROWID")
                outputs = {name: stack.enter_context((folder / (name + ".jsonl.part")).open("wb")) for name in ("train", "validation", "test")}
                sample, fields, output_bytes = [], {}, 0
                for row in self._rows(source, hasher):
                    stats["read"] += 1
                    if stats["read"] > MAX_ROWS:
                        raise ValueError("Dataset exceeds one million records; split the source first")
                    row = normalize(row)
                    if empty(row):
                        stats["empty"] += 1
                        continue
                    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    if len(encoded) > MAX_RECORD:
                        raise ValueError("Normalized record exceeds 1 MiB")
                    flagged = bool(EMAIL.search(encoded.decode("utf-8")))
                    stats["email_flagged"] += int(flagged)
                    if flagged and state["recipe"]["exclude_email"]:
                        stats["email_excluded"] += 1
                        continue
                    key = hashlib.sha256(encoded).hexdigest()
                    if db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (key,)).rowcount == 0:
                        stats["duplicates"] += 1
                        continue
                    split = int(key, 16) % 100
                    name = "train" if split < 80 else "validation" if split < 90 else "test"
                    output_bytes += len(encoded) + 1
                    if output_bytes > MAX_OUTPUT:
                        raise ValueError("Output exceeds 2 GiB")
                    outputs[name].write(encoded + b"\n")
                    stats[name] += 1
                    stats["kept"] += 1
                    if len(sample) < 3:
                        sample.append(encoded[:2000].decode("utf-8", errors="replace"))
                    for field, value in row.items():
                        # Bound schema observations even if each row has different field names.
                        if field in fields or len(fields) < 256:
                            fields.setdefault(field, set()).add(type(value).__name__)
                    if stats["read"] % 500 == 0:
                        self._check()
                        self._save(folder, state)
                        db.commit()
                for output in outputs.values():
                    output.flush()
                    os.fsync(output.fileno())
            if stats["kept"] == 0:
                raise ValueError("No records remain after cleaning; outputs not published")
            # A second bounded read verifies that the processed source stayed stable.
            state["status"] = "VERIFYING"
            self._save(folder, state)
            self._check()
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or digest(source) != hasher.hexdigest():
                raise ValueError("Source changed during processing; outputs not published")
            state["source_sha256"] = hasher.hexdigest()
            state["schema"] = {key: sorted(value) for key, value in fields.items()}
            state["schema_truncated"] = len(fields) == 256
            state["sample"] = sample
            for name in ("train", "validation", "test"):
                os.replace(folder / (name + ".jsonl.part"), folder / (name + ".jsonl"))
            card = ("# RainForge dataset\n\nSource: " + source.name + "\n\nSHA-256: " + state["source_sha256"] +
                    "\n\nDeclared license (not independently verified): " + state["license"] +
                    "\n\nProvenance: " + state["provenance"] + "\n\nRecipe: " + json.dumps(state["recipe"], ensure_ascii=False) +
                    "\n\nStatistics: " + json.dumps(stats) +
                    "\n\nEmail detection is a heuristic, not comprehensive personal-data screening. Small datasets may have empty splits. "
                    "This pipeline does not grant publishing or training rights.\n")
            (folder / "dataset-card.md").write_text(card, encoding="utf-8")
            state["files"] = [{"name": name, "bytes": (folder / name).stat().st_size, "sha256": digest(folder / name)} for name in FILES]
            # Verification reads every emitted JSONL record and recomputes split membership/counts.
            verified = {"train": 0, "validation": 0, "test": 0}
            for split in verified:
                with (folder / (split + ".jsonl")).open("rb") as stream:
                    for line in stream:
                        self._check()
                        json.loads(line)
                        position = int(hashlib.sha256(line.rstrip(b"\n")).hexdigest(), 16) % 100
                        expected = "train" if position < 80 else "validation" if position < 90 else "test"
                        if expected != split:
                            raise ValueError("Output split verification failed")
                        verified[split] += 1
            if any(verified[name] != stats[name] for name in verified):
                raise ValueError("Output record count verification failed")
            self._check()
            state.update(status="COMPLETED", verification={"counts": verified, "source_stable": True, "file_hashes": True})
        except InterruptedError as exc:
            state.update(status="CANCELLED", error=str(exc))
        except Exception as exc:
            state.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            # Only this run's scratch database is removed; original inputs are retained.
            (folder / "dedup.sqlite").unlink(missing_ok=True)
            self._save(folder, state)

    def read(self, run_id, name):
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id) or name not in FILES:
            raise ValueError("Unknown dataset artifact")
        folder = self.root / run_id
        with self.guard:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        if state["status"] != "COMPLETED":
            raise ValueError("Run is not verified")
        entry = next(item for item in state["files"] if item["name"] == name)
        path = folder / name
        if path.stat().st_size > 60000:
            raise ValueError("Artifact exceeds browser export limit (60 KB); use the displayed local output folder")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Artifact hash mismatch")
        return {"text": data.decode("utf-8"), "sha256": entry["sha256"], "verified": True}

    def close(self):
        self.cancel()
        self.upload_cancel()
        if self.thread:
            self.thread.join(timeout=2)
