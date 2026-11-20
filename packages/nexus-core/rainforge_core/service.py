from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import re
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rainforge_security import WorkspacePolicy, PolicyError
from rainforge_tasks import Task, TaskGraph, Status


def now():
    return datetime.now(timezone.utc).isoformat()


class NexusService:
    """A deliberately narrow, verified workflow: goal -> file -> hash proof.

    No model-generated command is executed. Unknown goals remain unsupported.
    SQLite is the source of truth; file creation is idempotent by content hash.
    """
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.policy = WorkspacePolicy(data_dir / "workspace")
        self.db_path = data_dir / "nexus.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversation_messages_thread ON conversation_messages(conversation_id,seq);
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    goal TEXT NOT NULL, content TEXT NOT NULL, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    graph TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, workflow_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL, verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
                    time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_memory (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT NOT NULL,
                    sha256 TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_memory_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO agent_memory_settings(singleton,enabled) VALUES(1,0);
                PRAGMA user_version=1;
            """)
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts USING fts5(id UNINDEXED,text)")
                db.execute("INSERT INTO agent_memory_fts SELECT id,text FROM agent_memory WHERE id NOT IN (SELECT id FROM agent_memory_fts)")
                self.memory_fts = True
            except sqlite3.OperationalError:
                self.memory_fts = False
        with self.connect() as db:
            if "auto_capture" not in {r[1] for r in db.execute("PRAGMA table_info(agent_memory_settings)")}:
                db.execute("ALTER TABLE agent_memory_settings ADD COLUMN auto_capture INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE conversation_messages SET state='interrupted' WHERE state='running'")
        self.recover()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, workflow_id, kind, detail):
        db.execute("INSERT INTO events(workflow_id,time,kind,detail) VALUES (?,?,?,?)",
                   (workflow_id, now(), kind, detail))

    def conversation_create(self):
        key=uuid4().hex
        with self.connect() as db:db.execute('INSERT INTO conversations VALUES(?,?,?)',(key,'Новый чат',__import__('time').time()))
        return {'id':key}

    def conversation_list(self,offset=0,limit=30):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid history page')
        with self.connect() as db:
            rows=db.execute('SELECT c.*, (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id=c.id) AS messages FROM conversations c ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?',(limit+1,offset)).fetchall()
        return {'items':[dict(r) for r in rows[:limit]],'has_more':len(rows)>limit}

    def conversation_read(self,conversation_id,before=None,limit=50):
        if not isinstance(conversation_id,str) or len(conversation_id)!=32 or any(c not in '0123456789abcdef' for c in conversation_id):raise ValueError('Invalid conversation')
        if before is not None and (type(before) is not int or before<1):raise ValueError('Invalid message cursor')
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid message page')
        with self.connect() as db:
            thread=db.execute('SELECT * FROM conversations WHERE id=?',(conversation_id,)).fetchone()
            if not thread:raise ValueError('Чат не найден')
            rows=db.execute('SELECT * FROM conversation_messages WHERE conversation_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(conversation_id,before or 9223372036854775807,limit+1)).fetchall()
        return {**dict(thread),'messages':[dict(r) for r in reversed(rows[:limit])],'has_more':len(rows)>limit}

    def conversation_begin(self,conversation_id,text,kind='chat'):
        if not isinstance(text,str) or not 1<=len(text)<=12000 or kind not in ('chat','action'):raise ValueError('Invalid conversation message')
        self.conversation_read(conversation_id,limit=1)
        with self.lock,self.connect() as db:
            if db.execute("SELECT 1 FROM conversation_messages WHERE conversation_id=? AND state='running'",(conversation_id,)).fetchone():raise ValueError('Дождитесь ответа или остановите текущую задачу в этом чате')
            count=db.execute('SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?',(conversation_id,)).fetchone()[0]
            stamp=__import__('time').time()
            db.execute('INSERT INTO conversation_messages(conversation_id,role,text,state,kind,created) VALUES(?,?,?,?,?,?)',(conversation_id,'user',text,'completed',kind,stamp))
            seq=db.execute('INSERT INTO conversation_messages(conversation_id,role,text,state,kind,created) VALUES(?,?,?,?,?,?)',(conversation_id,'assistant','','running',kind,stamp)).lastrowid
            db.execute('UPDATE conversations SET updated=?,title=CASE WHEN ?=0 THEN ? ELSE title END WHERE id=?',(stamp,count,text[:80],conversation_id))
        return seq

    def conversation_update(self,conversation_id,seq,text,state):
        if not isinstance(text,str) or len(text)>120000:raise ValueError('Conversation response too long')
        if state not in ('running','completed','cancelled','failed','interrupted'):raise ValueError('Invalid response state')
        with self.lock,self.connect() as db:
            row=db.execute("SELECT state FROM conversation_messages WHERE conversation_id=? AND seq=? AND role='assistant'",(conversation_id,seq)).fetchone()
            if not row:raise ValueError('Conversation response not found')
            if row['state']!='running':return
            db.execute('UPDATE conversation_messages SET text=?,state=? WHERE seq=?',(text,state,seq))
            db.execute('UPDATE conversations SET updated=? WHERE id=?',(__import__('time').time(),conversation_id))

    def conversation_context(self,conversation_id,before=None,budget=4500):
        page=self.conversation_read(conversation_id,before,limit=30)
        parts=[];size=0
        for message in reversed(page['messages']):
            if message['state']!='completed':continue
            piece=message['role']+': '+message['text'][-2000:]
            if size+len(piece)>budget:break
            parts.append(piece);size+=len(piece)
        return '\n'.join(reversed(parts))

    def conversation_chat_history(self, conversation_id, before=None, budget=4500):
        """Return whole completed turns, preserving roles for text inference."""
        messages = self.conversation_read(conversation_id, before, limit=30)['messages']
        history = []
        for index in range(len(messages)-2, -1, -1):
            user, assistant = messages[index:index+2]
            if user['role'] != 'user' or assistant['role'] != 'assistant':
                continue
            if user['state'] != 'completed' or assistant['state'] != 'completed':
                continue
            turn = [{'role': item['role'], 'content': item['text'][-2000:]}
                    for item in (user, assistant)]
            size = sum(len(item['content']) for item in turn)
            if size > budget:
                break
            history.extend(reversed(turn))
            budget -= size
        return list(reversed(history))

    def memory_list(self, query=""):
        if not isinstance(query,str) or len(query)>200:
            raise ValueError("Поисковый запрос слишком длинный")
        with self.lock,self.connect() as db:
            settings=db.execute("SELECT enabled,auto_capture FROM agent_memory_settings WHERE singleton=1").fetchone()
            enabled,auto_capture=bool(settings[0]),bool(settings[1])
            words=re.findall(r'\w+',query,flags=re.UNICODE)[:12]
            if words and self.memory_fts:
                expression=" OR ".join('"'+word.replace('"','""')+'"' for word in words)
                notes=[dict(row) for row in db.execute("SELECT m.* FROM agent_memory_fts f JOIN agent_memory m ON m.id=f.id WHERE agent_memory_fts MATCH ? ORDER BY rank LIMIT 80",(expression,))]
            elif words:
                notes=[dict(row) for row in db.execute("SELECT * FROM agent_memory WHERE text LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT 80",("%"+query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%",))]
            else:
                notes=[dict(row) for row in db.execute("SELECT * FROM agent_memory ORDER BY updated_at DESC LIMIT 80")]
        return {"enabled":enabled,"auto_capture":auto_capture,"notes":notes,"search_backend":"FTS5" if self.memory_fts else "SQLite LIKE","character_limit":12000}

    def memory_enable(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with self.lock,self.connect() as db:
            db.execute("UPDATE agent_memory_settings SET enabled=? WHERE singleton=1",(int(enabled),))
        return self.memory_list()

    def memory_configure(self, auto_capture):
        """Only the authenticated user-facing settings endpoint can opt in."""
        if type(auto_capture) is not bool:raise ValueError("auto_capture must be boolean")
        with self.lock,self.connect() as db:
            db.execute("UPDATE agent_memory_settings SET auto_capture=? WHERE singleton=1",(int(auto_capture),))
        return self.memory_list()

    @staticmethod
    def _memory_sensitive(text):
        # A conservative local rejection filter, not a comprehensive secret scanner.
        return bool(re.search(r"(?i)(?:password|passwd|парол[ьяи]|api[_ -]?key|api[_ -]?ключ|access[_ -]?token|refresh[_ -]?token|bearer|private[_ -]?key|секрет|токен|secret)\b\s*[:=]?\s*\S+|-----BEGIN|(?:sk-|ghp_|github_pat_|hf_)[A-Za-z0-9_-]{8,}|\b\d{3}-\d{2}-\d{4}\b|(?:(?:card|visa|mastercard|номер\s+карты|карта)\s*[:=]?\s*)(?:\d[ -]?){13,19}",text))

    def memory_feedback(self, text):
        """An explicit user-authored lesson, never called on model/tool output."""
        if not isinstance(text,str) or not 1<=len(text.strip())<=1400:
            raise ValueError("Исправление должно содержать 1–1400 символов")
        if self._memory_sensitive(text):raise ValueError("Не сохраняйте пароли, токены или платёжные данные в памяти")
        value="User correction: "+text.strip()
        with self.lock:
            old=next((n for n in self.memory_list()["notes"] if n["text"].casefold()==value.casefold() and n["state"]=="approved"),None)
            return old or self.memory_save(value)

    def memory_capture_user(self, text):
        """Opt-in capture of explicit first-party statements; no model inference.

        Invoke only at the original user input boundary, never for website,
        connector, model, history or agent-generated content. Full memory is
        reported rather than silently evicting user notes.
        """
        if not isinstance(text,str) or not 1<=len(text)<=12000:return {"state":"ignored"}
        with self.lock:
            config=self.memory_list()
            if not config["enabled"] or not config["auto_capture"]:return {"state":"disabled"}
            value=text.strip()
            match=re.fullmatch(r"(?is)(?:please\s+)?(?:remember(?:\s+that)?|запомни(?:те)?(?:,?\s+что)?)\s*[:,-]?\s+(.{1,1400})",value)
            kind="User preference: "
            if not match:
                match=re.fullmatch(r"(?is)(?:correction|feedback|исправление|поправка|учти(?:,?\s+что)?)\s*[:,-]\s*(.{1,1400})",value)
                kind="User correction: "
            if not match:return {"state":"ignored"}
            fact=match.group(1).strip()
            if self._memory_sensitive(fact):return {"state":"sensitive_skipped"}
            # Commands to bypass permissions must not become durable authorization.
            if re.search(r"(?i)(?:ignore|bypass|disable).{0,30}(?:permission|approval|safety)|(?:обход|отключ|игнорир).{0,30}(?:разрешени|подтверждени|защит)",fact):return {"state":"permission_skipped"}
            value=kind+fact
            old=next((n for n in config["notes"] if n["text"].casefold()==value.casefold() and n["state"]=="approved"),None)
            if old:return {"state":"duplicate","note_id":old["id"]}
            try:note=self.memory_save(value)
            except ValueError:return {"state":"full"}
            return {"state":"saved","note_id":note["id"]}

    def _memory_write(self,text,note_id=None,expected_sha256=None,state="approved"):
        if not isinstance(text,str) or not 1<=len(text.strip())<=1600:
            raise ValueError("Заметка должна содержать1–1600 символов")
        text=text.strip();digest=hashlib.sha256(text.encode()).hexdigest()
        with self.lock,self.connect() as db:
            current=db.execute("SELECT * FROM agent_memory WHERE id=?",(note_id,)).fetchone() if note_id else None
            if note_id and (not current or current["sha256"]!=expected_sha256):
                raise ValueError("Заметка изменилась; обновите список перед редактированием")
            count,total=db.execute("SELECT COUNT(*),COALESCE(SUM(LENGTH(text)),0) FROM agent_memory").fetchone()
            if (not current and count>=80) or total-(len(current["text"]) if current else 0)+len(text)>12000:
                raise ValueError("Память заполнена. Сократите или удалите ненужные заметки.")
            key=note_id or uuid4().hex
            db.execute("INSERT OR REPLACE INTO agent_memory VALUES(?,?,?,?,?)",(key,text,state,digest,now()))
            if self.memory_fts:
                db.execute("DELETE FROM agent_memory_fts WHERE id=?",(key,))
                db.execute("INSERT INTO agent_memory_fts(id,text) VALUES(?,?)",(key,text))
        return {"id":key,"text":text,"state":state,"sha256":digest}

    def memory_save(self,text,note_id=None,expected_sha256=None):
        return self._memory_write(text,note_id,expected_sha256,"approved")

    def memory_propose(self,text):
        # A model can propose a note but cannot approve it or enable recall.
        return self._memory_write(text,state="pending")

    def memory_approve(self,note_id,expected_sha256):
        with self.lock,self.connect() as db:
            row=db.execute("SELECT * FROM agent_memory WHERE id=?",(note_id,)).fetchone()
            if not row or row["sha256"]!=expected_sha256:
                raise ValueError("Предложение изменилось; обновите список")
            db.execute("UPDATE agent_memory SET state='approved',updated_at=? WHERE id=?",(now(),note_id))
        return self.memory_list()

    def memory_remove(self,note_id,expected_sha256):
        with self.lock,self.connect() as db:
            row=db.execute("SELECT sha256 FROM agent_memory WHERE id=?",(note_id,)).fetchone()
            if not row or row["sha256"]!=expected_sha256:
                raise ValueError("Заметка изменилась; обновите список")
            db.execute("DELETE FROM agent_memory WHERE id=?",(note_id,))
            if self.memory_fts:db.execute("DELETE FROM agent_memory_fts WHERE id=?",(note_id,))
        return self.memory_list()

    def memory_context(self,query):
        if not isinstance(query,str) or len(query)>12000:raise ValueError("Context query must contain at most12000 characters")
        selected=self.memory_list(query[:200])
        if not selected["enabled"]:return ""
        notes=[n["text"] for n in selected["notes"] if n["state"]=="approved"]
        recent=[n["text"] for n in self.memory_list()["notes"] if n["state"]=="approved"]
        # Keep the latest explicit lesson available even when search matches a
        # different preference; it remains context and grants no permissions.
        corrections=[text for text in recent if text.startswith("User correction: ")][:1]
        notes=corrections+[text for text in (notes or recent) if text not in corrections]
        return "\n".join(notes[:3])[:1600]

    def snapshot(self):
        with self.connect() as db:
            workflows = [dict(row) for row in db.execute("SELECT id,goal,status,created_at,graph,error FROM workflows ORDER BY created_at DESC LIMIT 100")]
            for row in workflows:
                row["graph"] = json.loads(row["graph"])
            artifacts = [dict(row) for row in db.execute("SELECT * FROM artifacts ORDER BY verified_at DESC LIMIT 100")]
        return {"workflows": workflows, "artifacts": artifacts, "workspace": str(self.policy.root)}

    def submit(self, goal: str, request_id: str):
        if not isinstance(goal, str) or not 1 <= len(goal) <= 12000:
            raise ValueError("Goal must contain 1–12000 characters")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("request_id must contain 1–100 characters")
        prefix = next((p for p in ("/note ", "Создай заметку:", "Create a note:") if goal.startswith(p)), None)
        if prefix is None or not goal[len(prefix):].strip():
            raise ValueError("Supported workflow: /note <text> or Создай заметку: <текст>. Use local chat for other prompts.")
        with self.lock:
            with self.connect() as db:
                existing = db.execute("SELECT id,goal FROM workflows WHERE request_id=?", (request_id,)).fetchone()
                if existing and existing["goal"] != goal:
                    raise ValueError("request_id was already used for a different goal")
                if existing:
                    return self.get(existing["id"])
                key = uuid4().hex
                content = f"# RainForge note\n\n{goal[len(prefix):].strip()}\n"
                path = f"artifacts/{key}.md"
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                graph = TaskGraph([
                    Task("write", goal, "Create a UTF-8 Markdown artifact", tools=["WRITE_FILE"], expected_result=path, verification=digest),
                    Task("verify", goal, "Read back and verify SHA-256", dependencies=["write"], tools=["VERIFY_RESULT"], agent="verification", expected_result=digest, verification="sha256 equality"),
                ])
                db.execute("INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (key, request_id, goal, content, path, digest, Status.PENDING, now(), json.dumps(graph.serialize()), None))
                self.event(db, key, "compiled", "Deterministic note workflow")
            self._execute(key)
            return self.get(key)

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT id,goal,status,created_at,graph,error FROM workflows WHERE id=?", (key,)).fetchone()
            if row is None:
                raise ValueError("Unknown workflow")
            result = dict(row)
            result["graph"] = json.loads(result["graph"])
            result["events"] = [dict(r) for r in db.execute("SELECT * FROM events WHERE workflow_id=? ORDER BY id", (key,))]
            result["artifacts"] = [dict(r) for r in db.execute("SELECT * FROM artifacts WHERE workflow_id=?", (key,))]
            return result

    def _save(self, key, graph, status, kind, detail, error=None):
        with self.connect() as db:
            db.execute("UPDATE workflows SET graph=?,status=?,error=? WHERE id=?",
                       (json.dumps(graph.serialize()), status, error, key))
            self.event(db, key, kind, detail)

    def _execute(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM workflows WHERE id=?", (key,)).fetchone()
        graph = TaskGraph([Task(**item) for item in json.loads(row["graph"])])
        try:
            path = self.policy.resolve(row["path"], "CREATE")
            for task in graph.tasks.values():
                if task.status in {Status.RUNNING, Status.VERIFYING}:
                    task.status = Status.RECOVERING
            while ready := graph.ready():
                task = ready[0]
                task.attempts += 1
                task.status = Status.RUNNING
                task.history.append({"time": now(), "status": Status.RUNNING})
                self._save(key, graph, Status.RUNNING, task.id, "started")
                if task.id == "write":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists():
                        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                            raise PolicyError("Existing artifact differs; automatic overwrite refused")
                    else:
                        with path.open("xb") as stream:
                            stream.write(row["content"].encode("utf-8"))
                            stream.flush()
                            __import__("os").fsync(stream.fileno())
                else:
                    task.status = Status.VERIFYING
                    self._save(key, graph, Status.VERIFYING, task.id, "hashing file")
                    payload = path.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                        raise ValueError("Artifact verification failed")
                    artifact = {"id": key, "path": row["path"], "sha256": row["sha256"], "bytes": len(payload)}
                    task.artifacts.append(artifact)
                    with self.connect() as db:
                        db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?)",
                                   (key, key, path.name, row["path"], row["sha256"], len(payload), now()))
                task.status = Status.COMPLETED
                task.history.append({"time": now(), "status": Status.COMPLETED})
                self._save(key, graph, Status.RUNNING, task.id, "completed")
            status = Status.COMPLETED if all(t.status == Status.COMPLETED for t in graph.tasks.values()) else Status.FAILED
            self._save(key, graph, status, "workflow", status)
        except (OSError, ValueError) as exc:
            for task in graph.tasks.values():
                if task.status in {Status.RUNNING, Status.VERIFYING}:
                    task.status = Status.BLOCKED if isinstance(exc, PolicyError) else Status.FAILED
            self._save(key, graph, Status.BLOCKED if isinstance(exc, PolicyError) else Status.FAILED, "error", str(exc), str(exc))

    def recover(self):
        with self.lock, self.connect() as db:
            keys = [r["id"] for r in db.execute("SELECT id FROM workflows WHERE status IN ('PENDING','RUNNING','VERIFYING','RECOVERING')")]
        for key in keys:
            self._execute(key)

    def read_artifact(self, artifact_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown artifact")
        path = self.policy.resolve(row["path"])
        if path.stat().st_size > 1_000_000:
            raise ValueError("Artifact preview limit exceeded")
        data = path.read_bytes()
        return {"text": data.decode("utf-8"), "verified": hashlib.sha256(data).hexdigest() == row["sha256"], **dict(row)}
