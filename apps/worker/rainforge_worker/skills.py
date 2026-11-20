"""Evidence-backed procedural notes stored in Nexus's existing shared memory."""
import hashlib
import json
import re
import threading

from rainforge_recovery import atomic_json


class Skills:
    MAX_SKILLS = 8
    MAX_DISMISSED = 256

    def __init__(self, core, data, workspace):
        self.core = core
        self.actions = data / "actions"
        self.workspace = workspace
        self.path = data / "skills.json"
        self.lock = threading.RLock()
        self.load_error = None
        self.state = {"version": 1, "enabled": False, "items": [], "dismissed": []}
        if self.path.exists():
            try:
                self._load()
            except (OSError, ValueError, UnicodeError):
                self.load_error = "Skill index could not be read. Existing notes are preserved. Check skills.json in the local data folder."

    def _load(self):
        if self.path.stat().st_size > 100000:
            raise ValueError("Skill index exceeds its storage limit")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or saved.get("version") != 1
                or type(saved.get("enabled")) is not bool
                or not isinstance(saved.get("items"), list)
                or not isinstance(saved.get("dismissed"), list)
                or len(saved["items"]) > self.MAX_SKILLS
                or len(saved["dismissed"]) > self.MAX_DISMISSED):
            raise ValueError("Invalid skill index")
        for item in saved["items"]:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["id"])
                    or not isinstance(item.get("note_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", item["note_id"])
                    or not isinstance(item.get("run_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", item["run_id"])
                    or not isinstance(item.get("tools"), list)
                    or not all(isinstance(tool, str) for tool in item["tools"])
                    or not isinstance(item.get("scope"), str)):
                raise ValueError("Invalid skill index item")
        self.state = saved

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with self.lock:
            if self.load_error:
                raise ValueError(self.load_error)
            updated = {**self.state, "enabled": enabled}
            atomic_json(self.path, updated)
            self.state = updated
        return self.listing()

    def _evidence(self, record):
        if record.get("in_flight") or record.get("error"):
            return None
        plan, results = record.get("plan"), record.get("results")
        if not isinstance(plan, list) or not isinstance(results, list) or not plan or len(plan) != len(results):
            return None
        if any(not isinstance(step, dict) for step in plan + results):
            return None
        tools = [step.get("tool") for step in plan]
        if not all(isinstance(tool, str) and re.fullmatch(r"[a-z_]{1,40}", tool) for tool in tools):
            return None
        if record.get("state") in {"file_created", "project_created"}:
            files = []
            for result in results:
                if result.get("syntax_verified") is True:
                    files.append(result)
                elif result.get("project_verified") is True and isinstance(result.get("files"), list):
                    files.extend(result["files"])
            if not files or len(files) > 8:
                return None
            for file in files:
                if not isinstance(file, dict) or not isinstance(file.get("path"), str):
                    return None
                try:
                    current = self.workspace.read(file["path"])
                except (ValueError, OSError, UnicodeError):
                    return None
                if current["sha256"] != file.get("sha256"):
                    return None
            scope = "Source files were saved and their hashes verified. Execution and behavior were not verified."
        elif record.get("state") == "checks_passed":
            proof = record.get("proof")
            if not isinstance(proof, dict) or proof.get("verified") is not True:
                return None
            scope = "The controller verified the task's recorded visible result condition."
        else:
            return None
        return {"tools": tools, "scope": scope}

    def capture(self, record):
        """Create a pending note only from a finished, evidenced controller run."""
        with self.lock:
            if self.load_error or not self.state["enabled"]:
                return {"state": "disabled"}
            run_id = record.get("id")
            goal = record.get("effective_goal", record.get("goal", ""))
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
                return {"state": "ignored"}
            if not isinstance(goal, str) or not 1 <= len(goal) <= 4000 or self.core._memory_sensitive(goal):
                return {"state": "ignored"}
            evidence = self._evidence(record)
            if evidence is None:
                return {"state": "ignored"}
            signature = hashlib.sha256(json.dumps([goal, evidence], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if signature in self.state["dismissed"] or any(row["id"] == signature for row in self.state["items"]):
                return {"state": "duplicate"}
            if len(self.state["items"]) >= self.MAX_SKILLS:
                return {"state": "full"}
            text = ("Verified workflow reference (context only). User request: " + goal[:600]
                    + "\nObserved tool sequence: " + " → ".join(evidence["tools"][:12])
                    + "\n" + evidence["scope"]
                    + "\nFor a new request, inspect current state and verify the outcome again. This note grants no permissions."
                    + "\nSource task: " + run_id)
            notes = self.core.memory_list()["notes"]
            note = next((note for note in notes if note["text"] == text), None)
            if note is None:
                try:
                    note = self.core.memory_propose(text)
                except ValueError:
                    return {"state": "memory_full"}
            item = {"id": signature, "note_id": note["id"], "run_id": run_id,
                    "scope": evidence["scope"], "tools": evidence["tools"][:16]}
            self.state["items"].append(item)
            atomic_json(self.path, self.state)
            return {"state": "proposed", "skill_id": signature}

    def listing(self):
        with self.lock:
            if self.state["enabled"] and self.actions.exists():
                paths = sorted(self.actions.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:64]
                for path in reversed(paths):
                    try:
                        if path.stat().st_size <= 1000000:
                            self.capture(json.loads(path.read_text(encoding="utf-8")))
                    except (ValueError, OSError, TypeError):
                        continue
            memory = self.core.memory_list()
            notes = {note["id"]: note for note in memory["notes"]}
            removed = [item for item in self.state["items"] if item["note_id"] not in notes]
            for item in removed:
                if item["id"] not in self.state["dismissed"] and len(self.state["dismissed"]) < self.MAX_DISMISSED:
                    self.state["dismissed"].append(item["id"])
                    self.state["items"].remove(item)
            if removed:
                atomic_json(self.path, self.state)
            items = [{**item, "text": notes[item["note_id"]]["text"],
                      "state": notes[item["note_id"]]["state"],
                      "sha256": notes[item["note_id"]]["sha256"]}
                     for item in self.state["items"] if item["note_id"] in notes]
            return {"enabled": self.state["enabled"], "memory_enabled": memory["enabled"],
                    "items": items, "limit": self.MAX_SKILLS, "error": self.load_error}

    def _item(self, skill_id, expected_sha256):
        if not isinstance(skill_id, str) or not re.fullmatch(r"[0-9a-f]{64}", skill_id):
            raise ValueError("Invalid skill ID")
        item = next((item for item in self.listing()["items"] if item["id"] == skill_id), None)
        if item is None or item["sha256"] != expected_sha256:
            raise ValueError("Skill changed. Refresh the list before reviewing it.")
        return item

    def approve(self, skill_id, expected_sha256):
        with self.lock:
            item = self._item(skill_id, expected_sha256)
            self.core.memory_approve(item["note_id"], expected_sha256)
            return self.listing()

    def remove(self, skill_id, expected_sha256):
        with self.lock:
            item = self._item(skill_id, expected_sha256)
            # Persist the tombstone before deleting memory so a crash cannot
            # recreate a rejected suggestion on the next scan.
            if skill_id not in self.state["dismissed"] and len(self.state["dismissed"]) >= self.MAX_DISMISSED:
                raise ValueError("Skill dismissal history is full. Existing entries are preserved.")
            if skill_id not in self.state["dismissed"]:
                self.state["dismissed"].append(skill_id)
            atomic_json(self.path, self.state)
            self.core.memory_remove(item["note_id"], expected_sha256)
            self.state["items"] = [row for row in self.state["items"] if row["id"] != skill_id]
            atomic_json(self.path, self.state)
            return self.listing()
