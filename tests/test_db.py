"""Phase 2 tests: SQLite repository behavior."""

from __future__ import annotations

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
