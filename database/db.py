"""Local SQLite state: conversations, courses, assignments, files, approvals.

A single connection guarded by a re-entrant lock is enough for one local user
and avoids the complexity of an async ORM. WAL mode keeps reads responsive
while the agent is writing an event log entry.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    moodle_id TEXT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    due_text TEXT,
    status_text TEXT,
    workspace_path TEXT,
    last_opened_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments(course_id);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER REFERENCES assignments(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_assignment ON files(assignment_id);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    """Synchronous SQLite repository. Safe to call from worker threads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- conversations & messages ----------

    def create_conversation(self, title: str = "New conversation") -> int:
        now = _utc_now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations(title, created_at, updated_at) VALUES (?,?,?)",
                (title[:120], now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def ensure_conversation(self, conversation_id: int | None, first_message: str) -> int:
        if conversation_id is None:
            title = " ".join(first_message.split())[:70] or "New conversation"
            return self.create_conversation(title)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Conversation {conversation_id} does not exist")
        return conversation_id

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        tool_calls: Any | None = None,
    ) -> int:
        now = _utc_now()
        payload = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(conversation_id, role, content, tool_calls, created_at) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, role, content, payload, now),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_messages(self, conversation_id: int, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, tool_calls, created_at FROM messages "
                "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            item = dict(row)
            if item.get("tool_calls"):
                try:
                    item["tool_calls"] = json.loads(item["tool_calls"])
                except json.JSONDecodeError:
                    item["tool_calls"] = None
            result.append(item)
        return result

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def conversation_exists(self, conversation_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return row is not None

    # ---------- courses & assignments ----------

    def upsert_course(self, name: str, url: str, moodle_id: str | None = None) -> int:
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO courses(moodle_id, name, url, last_seen_at) VALUES (?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "name=excluded.name, moodle_id=COALESCE(excluded.moodle_id, courses.moodle_id), "
                "last_seen_at=excluded.last_seen_at",
                (moodle_id, name, url, now),
            )
            row = self._conn.execute("SELECT id FROM courses WHERE url=?", (url,)).fetchone()
            self._conn.commit()
            return int(row["id"])

    def list_courses(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, moodle_id, name, url, last_seen_at FROM courses "
                "ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM courses WHERE id=?", (course_id,)
            ).fetchone()
        return _as_dict(row)

    def upsert_assignment(
        self,
        course_id: int | None,
        name: str,
        url: str,
        due_text: str | None = None,
        status_text: str | None = None,
    ) -> int:
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO assignments(course_id, name, url, due_text, status_text, created_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "course_id=COALESCE(excluded.course_id, assignments.course_id), "
                "name=excluded.name, "
                "due_text=COALESCE(excluded.due_text, assignments.due_text), "
                "status_text=COALESCE(excluded.status_text, assignments.status_text)",
                (course_id, name, url, due_text, status_text, now),
            )
            row = self._conn.execute(
                "SELECT id FROM assignments WHERE url=?", (url,)
            ).fetchone()
            self._conn.commit()
            return int(row["id"])

    def set_assignment_workspace(self, assignment_id: int, workspace_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE assignments SET workspace_path=?, last_opened_at=? WHERE id=?",
                (workspace_path, _utc_now(), assignment_id),
            )
            self._conn.commit()

    def list_assignments(self, course_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if course_id is None:
                rows = self._conn.execute(
                    "SELECT a.*, c.name AS course_name FROM assignments a "
                    "LEFT JOIN courses c ON c.id=a.course_id "
                    "ORDER BY a.id DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT a.*, c.name AS course_name FROM assignments a "
                    "LEFT JOIN courses c ON c.id=a.course_id "
                    "WHERE a.course_id=? ORDER BY a.id DESC",
                    (course_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ---------- files ----------

    def record_file(
        self,
        path: str,
        size_bytes: int,
        kind: str,
        assignment_id: int | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO files(assignment_id, kind, path, size_bytes, created_at) "
                "VALUES (?,?,?,?,?)",
                (assignment_id, kind, path, size_bytes, _utc_now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_files(self, assignment_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            if assignment_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM files ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM files WHERE assignment_id=? ORDER BY id DESC LIMIT ?",
                    (assignment_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    # ---------- activity ----------

    def log_activity(
        self,
        level: str,
        kind: str,
        message: str,
        data: Any | None = None,
    ) -> int:
        payload = json.dumps(data, ensure_ascii=False, default=str) if data else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO activity_log(level, kind, message, data, created_at) "
                "VALUES (?,?,?,?,?)",
                (level, kind, message, payload, _utc_now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_activity(self, limit: int = 150) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        items = []
        for row in reversed(rows):
            item = dict(row)
            if item.get("data"):
                try:
                    item["data"] = json.loads(item["data"])
                except json.JSONDecodeError:
                    item["data"] = None
            items.append(item)
        return items

    # ---------- approvals (audit trail) ----------

    def create_approval(
        self, approval_id: str, run_id: str | None, action: str, details: dict[str, Any]
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals(id, run_id, action, details, status, created_at) "
                "VALUES (?,?,?,?, 'pending', ?)",
                (approval_id, run_id, action, json.dumps(details, ensure_ascii=False), _utc_now()),
            )
            self._conn.commit()

    def resolve_approval(self, approval_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE approvals SET status=?, resolved_at=? WHERE id=?",
                (status, _utc_now(), approval_id),
            )
            self._conn.commit()

    def pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at"
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item["details"])
            except json.JSONDecodeError:
                item["details"] = {}
            items.append(item)
        return items

    # ---------- reset ----------

    _RESET_TABLES = (
        "messages",
        "approvals",
        "files",
        "assignments",
        "courses",
        "conversations",
        "activity_log",
    )

    def reset_data(self) -> dict[str, int]:
        """Delete every row while keeping the schema in place.

        Children are cleared before parents so this works with
        ``PRAGMA foreign_keys=ON``. The autoincrement counters are reset too, so
        a fresh session starts at id 1 again. Returns rows removed per table.
        """

        counts: dict[str, int] = {}
        with self._lock:
            for table in self._RESET_TABLES:
                row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(row["n"]) if row is not None else 0
                self._conn.execute(f"DELETE FROM {table}")
            try:
                self._conn.execute("DELETE FROM sqlite_sequence")
            except sqlite3.OperationalError:
                # sqlite_sequence only exists once an AUTOINCREMENT row was written.
                pass
            self._conn.commit()
        return counts

    def get_recent_messages_for_prompt(
        self, conversation_id: int, limit: int = 12
    ) -> Iterable[dict[str, Any]]:
        """Chronological user/assistant messages, excluding tool transcripts."""
        messages = self.get_messages(conversation_id, limit=limit)
        return [m for m in messages if m["role"] in {"user", "assistant"} and m["content"]]
