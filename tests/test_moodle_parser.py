"""Phase 5 test: parse courses, assignments, and assignment details from live DOM.

Run with:  $env:RUN_BROWSER_TESTS="1"; python -m pytest tests/test_moodle_parser.py -q
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import load_settings
from browser.moodle import (
    parse_assignment_detail,
    parse_assignments,
    parse_courses,
    take_snapshot,
)
from browser.session import BrowserSession

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

COURSES = """<!doctype html><html><head><title>My courses</title></head>
<body class="path-my"><div id="region-main">
<h1>My courses</h1>
<a href="/course/view.php?id=2">Database Systems</a>
<script>const IGNORE_ME = "do not read this";</script>
</div>
<div class="moove-dashboard">
  <div class="course-card" data-course-id="3">
    <div class="course-name">Software Quality Engineering</div>
  </div>
</div>
</body></html>"""

COURSE = """<!doctype html><html><head><title>Database Systems</title></head>
<body class="path-course-view"><div id="region-main">
<h1>Database Systems</h1>
<ul>
<li class="activity modtype_assign">
  <div class="activityname"><a href="/mod/assign/view.php?id=10">Normalization Exercise</a></div>
  <div data-region="activity-dates">Due: 12 November 2026, 11:59 PM</div>
  <div class="submissionstatus">Not submitted</div>
</li>
<li class="activity modtype_resource">
  <div class="activityname"><a href="/mod/resource/view.php?id=11">Week 4 Slides</a></div>
</li>
</ul></div></body></html>"""

ASSIGNMENT = """<!doctype html><html><head><title>Normalization Exercise</title></head>
<body class="path-mod-assign"><div id="region-main">
<nav aria-label="Breadcrumb"><a href="/my/">Dashboard</a>
<a href="/course/view.php?id=2">Database Systems</a></nav>
<h1>Normalization Exercise</h1>
<div id="intro"><p>Normalize the relation to 3NF. Submit a PDF.</p></div>
<div data-region="activity-dates">Due: 12 November 2026, 11:59 PM</div>
<div class="submissionstatustable">Submission status: No attempt</div>
<a href="/pluginfile.php/99/mod_assign/intro/instructions.pdf">instructions.pdf</a>
</div></body></html>"""

PAGES = {
    "/my/courses.php": COURSES,
    "/course/view.php": COURSE,
    "/mod/assign/view.php": ASSIGNMENT,
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = PAGES.get(self.path.split("?")[0], COURSES).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def test_moodle_extraction(tmp_path: Path, mock_moodle: str) -> None:
    settings = replace(
        load_settings(),
        browser_profile_dir=tmp_path / "profile",
        moodle_url=None,
    )

    async def scenario() -> None:
        session = BrowserSession(settings)
        try:
            await session.start(f"{mock_moodle}/my/courses.php")
            snapshot = await take_snapshot(session.page)
            assert snapshot["page_type"] == "dashboard"
            assert "IGNORE_ME" not in snapshot["text"]

            courses = parse_courses(snapshot)
            assert [c["name"] for c in courses] == [
                "Database Systems",
                "Software Quality Engineering",
            ]
            assert courses[0]["course_id"] == "2"

            await session.navigate(courses[0]["url"])
            course_snapshot = await take_snapshot(session.page)
            assignments = parse_assignments(course_snapshot)
            assert len(assignments) == 1
            assert assignments[0]["name"] == "Normalization Exercise"
            assert assignments[0]["pending"] is True
            assert "12 November 2026" in assignments[0]["due_text"]
            assert assignments[0]["url"].endswith("/mod/assign/view.php?id=10")

            await session.navigate(assignments[0]["url"])
            detail_snapshot = await take_snapshot(session.page)
            assert detail_snapshot["page_type"] == "assignment"
            detail = parse_assignment_detail(detail_snapshot)
            assert detail["name"] == "Normalization Exercise"
            assert detail["course_name"] == "Database Systems"
            assert "3NF" in detail["instructions"]
            assert any(a["href"].endswith("instructions.pdf") for a in detail["attachments"])
            assert "No attempt" in detail["submission_status_text"]
        finally:
            await session.close()

    asyncio.run(scenario())
