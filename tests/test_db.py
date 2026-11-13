"""Phase 2 tests: SQLite repository behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def test_conversation_and_messages(db: Database) -> None:
    conversation_id = db.ensure_conversation(None, "Explain my SQL assignment")
    assert conversation_id > 0

    db.add_message(conversation_id, "user", "Explain my SQL assignment")
    db.add_message(
        conversation_id,
        "assistant",
        "",
        tool_calls=[{"function": {"name": "list_courses", "arguments": {}}}],
    )

    messages = db.get_messages(conversation_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "list_courses"

    conversations = db.list_conversations()
    assert conversations[0]["title"] == "Explain my SQL assignment"


def test_ensure_conversation_rejects_missing(db: Database) -> None:
    with pytest.raises(ValueError):
        db.ensure_conversation(999, "hello")


def test_course_and_assignment_upsert(db: Database) -> None:
    course_id = db.upsert_course(
        "Database Systems", "https://moodle.example.edu/course/view.php?id=2", "2"
    )
    same_course = db.upsert_course(
        "Database Systems (2026)", "https://moodle.example.edu/course/view.php?id=2", "2"
    )
    assert course_id == same_course
    assert db.list_courses()[0]["name"] == "Database Systems (2026)"

    assignment_id = db.upsert_assignment(
        course_id,
        "Normalization Exercise",
        "https://moodle.example.edu/mod/assign/view.php?id=10",
        "Due 12 Nov 2026",
        "Not submitted",
    )
    db.set_assignment_workspace(assignment_id, "Database Systems/Normalization Exercise")
    rows = db.list_assignments(course_id)
    assert len(rows) == 1
    assert rows[0]["workspace_path"].endswith("Normalization Exercise")
    assert rows[0]["course_name"] == "Database Systems (2026)"


def test_files_and_activity(db: Database) -> None:
    file_id = db.record_file("generated/report.docx", 1234, "docx")
    assert file_id > 0
    assert db.list_files()[0]["path"] == "generated/report.docx"

    db.log_activity("info", "browser", "Opened course page", {"url": "https://x"})
    activity = db.list_activity()
    assert activity[0]["kind"] == "browser"
    assert activity[0]["data"]["url"] == "https://x"


def test_approval_lifecycle(db: Database) -> None:
    db.create_approval("abc123", "run1", "overwrite_file", {"path": "x.txt"})
    pending = db.pending_approvals()
    assert len(pending) == 1
    assert pending[0]["details"]["path"] == "x.txt"

    db.resolve_approval("abc123", "approved")
    assert db.pending_approvals() == []


def test_reset_data_clears_every_table(db: Database) -> None:
    conversation_id = db.ensure_conversation(None, "hello")
    db.add_message(conversation_id, "user", "hello")
    course_id = db.upsert_course("Database Systems", "https://moodle.example.edu/course/view.php?id=2")
    db.upsert_assignment(course_id, "Lab 1", "https://moodle.example.edu/mod/assign/view.php?id=7")
    db.record_file("generated/notes.md", 10, "notes")
    db.log_activity("info", "browser", "Opened course page")
    db.create_approval("abc123", "run1", "overwrite_file", {"path": "x.txt"})

    counts = db.reset_data()

    assert counts["conversations"] == 1
    assert counts["messages"] == 1
    assert counts["courses"] == 1
    assert counts["assignments"] == 1
    assert counts["files"] == 1
    assert counts["activity_log"] == 1
    assert counts["approvals"] == 1

    assert db.list_courses() == []
    assert db.list_assignments() == []
    assert db.list_files() == []
    assert db.list_conversations() == []
    assert db.list_activity() == []
    assert db.pending_approvals() == []

    # Ids start from 1 again, so a fresh session looks brand new.
    assert db.create_conversation("fresh") == 1


def test_sync_overwrites_stale_status_and_keeps_workspace(db: Database) -> None:
    course_id = db.upsert_course(
        "Database Systems", "https://lms.example.edu/course/view.php?id=2", "2"
    )
    url = "https://lms.example.edu/mod/assign/view.php?id=10"
    assignment_id = db.upsert_assignment(
        course_id, "Normalization Exercise", url, "Due 12 Nov 2026", "Not submitted"
    )
    db.set_assignment_workspace(assignment_id, "Database Systems/Normalization Exercise")
    assert db.latest_sync_at() is None

    db.record_synced_assignment(
        course_id,
        "Normalization Exercise",
        url,
        due_text="Sunday, 15 November 2026, 11:59 PM",
        due_at="2026-11-15T18:59:00+00:00",
        status="submitted_for_grading",
        status_text="Submitted for grading",
    )

    row = db.list_assignments()[0]
    assert row["status"] == "submitted_for_grading"
    assert row["status_text"] == "Submitted for grading"
    assert row["due_at"] == "2026-11-15T18:59:00+00:00"
    assert row["workspace_path"].endswith("Normalization Exercise")
    assert db.latest_sync_at() is not None

    # A later sync replaces the status again instead of keeping the old value.
    same = db.record_synced_assignment(course_id, "Normalization Exercise", url, status="graded")
    assert same == assignment_id
    assert db.list_assignments()[0]["status"] == "graded"
    assert len(db.list_assignments()) == 1


def test_older_database_gains_the_sync_columns(tmp_path: Path) -> None:
    """A database created before the sync columns existed must keep working."""

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moodle_id TEXT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE assignments (
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
        INSERT INTO assignments(name, url, due_text, status_text, created_at)
        VALUES ('Old row', 'https://lms.example.edu/mod/assign/view.php?id=1',
                'Due 1 Nov 2026', 'Not submitted', '2026-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    try:
        row = database.list_assignments()[0]
        assert row["name"] == "Old row"
        assert row["status"] == "unknown"
        assert row["due_at"] is None
        assert row["last_synced_at"] is None

        database.record_synced_assignment(
            None,
            "Old row",
            "https://lms.example.edu/mod/assign/view.php?id=1",
            due_text="Sunday, 1 November 2026, 11:59 PM",
            due_at="2026-11-01T18:59:00+00:00",
            status="not_submitted",
            status_text="Not submitted",
        )
        migrated = database.list_assignments()[0]
        assert migrated["status"] == "not_submitted"
        assert migrated["due_at"] == "2026-11-01T18:59:00+00:00"
    finally:
        database.close()


def test_prune_courses_removes_the_course_and_its_assignments(db: Database) -> None:
    keep = db.upsert_course("Database Systems", "https://lms.test/course/view.php?id=2", "2")
    drop = db.upsert_course("Extra Lab", "https://lms.test/course/view.php?id=3", "3")
    db.upsert_assignment(keep, "Lab 1", "https://lms.test/mod/assign/view.php?id=10", None, None)
    db.upsert_assignment(drop, "Lab 2", "https://lms.test/mod/assign/view.php?id=11", None, None)

    assert db.prune_courses([drop]) == 1
    assert db.prune_courses([]) == 0
    assert [c["name"] for c in db.list_courses()] == ["Database Systems"]
    assert [a["name"] for a in db.list_assignments()] == ["Lab 1"]
