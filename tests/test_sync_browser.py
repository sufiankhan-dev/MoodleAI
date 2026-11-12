"""Live browser test for the automatic Moodle sync.

Run with:  $env:RUN_BROWSER_TESTS="1"; python -m pytest tests/test_sync_browser.py -q

It serves a small fake Moodle on loopback and drives a real visible Chrome,
which is the only way to cover the in-page ``fetch`` + ``DOMParser`` used to
read ``/mod/assign/index.php`` without navigating the user's tab.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import load_settings
from app.security import PathJail
from app.sync import sync_courses
from browser.session import BrowserSession
from database.db import Database

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

DASHBOARD = """<!doctype html><html><head><title>Dashboard</title></head>
<script>var M = {cfg: {sesskey: "testsess42"}};</script>
<body class="path-my"><div id="region-main">
<h1>Dashboard</h1>
<a href="/course/view.php?id=2">Database Systems</a>
<a href="/course/view.php?id=3">Software Engineering</a>
</div></body></html>"""

INDEX = """<!doctype html><html><head><title>Assignments</title></head>
<body class="path-mod-assign"><div id="region-main">
<h1>Database Systems</h1>
<table class="generaltable">
<thead><tr><th></th><th>Assignment name</th><th>Due date</th>
<th>Submission status</th><th>Grade</th></tr></thead>
<tbody>
<tr><td></td><td><a href="/mod/assign/view.php?id=10">Normalization Exercise</a></td>
<td><time datetime="2026-11-15T18:59:00+00:00">Sunday, 15 November 2026, 11:59 PM</time></td>
<td>Submitted for grading</td><td>85.00</td></tr>
<tr><td></td><td><a href="/mod/assign/view.php?id=11">ER Diagram</a></td>
<td>No due date</td><td>No attempt</td><td>-</td></tr>
<tr><td></td><td><a href="/mod/assign/view.php?id=12">Query Optimisation</a></td>
<td>Monday, 16 November 2026, 11:59 PM</td><td>No attempt</td><td>-</td></tr>
</tbody></table>
</div></body></html>"""

EMPTY_INDEX = """<!doctype html><html><head><title>Assignments</title></head>
<body class="path-mod-assign"><div id="region-main">
<h1>Software Engineering</h1><p>No assignments in this course.</p>
</div></body></html>"""


# What core_calendar_get_action_events_by_timesort returns for a student:
# one real due date, plus a teacher-side event and a quiz that must be ignored.
ACTION_EVENTS = {
    "events": [
        {
            "id": 501,
            "name": "Query Optimisation is due",
            "modulename": "assign",
            "eventtype": "due",
            "timesort": 1795219140,
            "url": "/mod/assign/view.php?id=12",
        },
        {
            "id": 502,
            "name": "ER Diagram grading",
            "modulename": "assign",
            "eventtype": "gradingdue",
            "timesort": 1795219140,
            "url": "/mod/assign/view.php?id=11",
        },
        {
            "id": 503,
            "name": "Quiz closes",
            "modulename": "quiz",
            "eventtype": "close",
            "timesort": 1795219140,
            "url": "/mod/quiz/view.php?id=40",
        },
    ],
    "firstid": 501,
    "lastid": 503,
}

SERVICE_CALLS: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"[]")
        SERVICE_CALLS.append({"path": self.path, "request": request})
        ok = (
            self.path.startswith("/lib/ajax/service.php")
            and "sesskey=testsess42" in self.path
            and request
            and request[0].get("methodname") == "core_calendar_get_action_events_by_timesort"
        )
        reply = (
            [{"error": False, "data": ACTION_EVENTS}]
            if ok
            else [{"error": True, "exception": {"errorcode": "invalidsesskey"}}]
        )
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/mod/assign/index.php":
            course_id = self.path.split("id=")[-1]
            body = INDEX if course_id == "2" else EMPTY_INDEX
        else:
            body = DASHBOARD
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture()
def mock_moodle() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sync_reads_status_and_due_dates(tmp_path: Path, mock_moodle: str) -> None:
    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "profile",
        moodle_url=None,
    )
    settings.ensure_directories()
    database = Database(settings.database_path)
    jail = PathJail(
        {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
    )
    events: list[tuple[str, str, dict | None]] = []
    SERVICE_CALLS.clear()

    async def scenario() -> None:
        session = BrowserSession(settings)
        try:
            await session.start(f"{mock_moodle}/my/")
            summary = await sync_courses(
                browser=session,
                db=database,
                settings=settings,
                jail=jail,
                emit=lambda kind, message, data: events.append((kind, message, data)),
            )
        finally:
            await session.close()

        assert summary["ok"] is True
        assert summary["courses"] == 2
        assert summary["courses_total"] == 2
        assert summary["assignments"] == 3
        assert summary["pending"] == 2
        assert summary["deadlines"] == 2
        assert summary["errors"] == []
        assert summary["notes"] == []

        rows = {row["name"]: row for row in database.list_assignments()}
        assert set(rows) == {"Normalization Exercise", "ER Diagram", "Query Optimisation"}
        submitted = rows["Normalization Exercise"]
        assert submitted["status"] == "submitted_for_grading"
        assert submitted["due_at"] == "2026-11-15T18:59:00+00:00"
        assert "15 November 2026" in submitted["due_text"]
        assert submitted["course_name"] == "Database Systems"

        pending = rows["ER Diagram"]
        assert pending["status"] == "not_submitted"
        assert pending["due_at"] is None

        # Display text alone never yields an instant; the calendar event does.
        calendar = rows["Query Optimisation"]
        assert calendar["status"] == "not_submitted"
        assert calendar["due_at"] == "2026-11-20T23:59:00+00:00"

        assert len(SERVICE_CALLS) == 1
        # The session key stays inside the page: it is in no event or summary.
        assert "testsess42" not in json.dumps(summary)
        assert all("testsess42" not in json.dumps(item) for item in events)
        assert database.latest_sync_at() is not None

        assert any(kind == "sync" for kind, _message, _data in events)

    asyncio.run(scenario())
    database.close()
