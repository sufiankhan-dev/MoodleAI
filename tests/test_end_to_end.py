"""Phase 10 end-to-end test against a mock Moodle site.

Run with:  $env:RUN_BROWSER_TESTS="1"; python -m pytest tests/test_end_to_end.py -q

A visible Chrome window opens, logs into a local mock Moodle, and the real tool
handlers run the complete study workflow: courses -> assignment -> PDF download
-> PDF reading -> Word/PDF/code generation -> workspace persistence. No model
call is made here, so the result is deterministic.
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
import pytest

from app.config import load_settings
from app.security import PathJail
from browser.moodle import parse_assignment_detail, take_snapshot
from browser.session import BrowserSession, BrowserState
from database.db import Database
from tools import build_registry
from tools.base import ToolContext, ToolError

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

COURSES = """<!doctype html><html><head><title>My courses</title></head>
<body class="path-my"><div id="region-main">
<h1>My courses</h1>
<a href="/course/view.php?id=2">Database Systems</a>
<a href="/course/view.php?id=3">Software Quality Engineering</a>
<a href="/login/logout.php">Log out</a>
</div></body></html>"""

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
</ul><a href="/login/logout.php">Log out</a>
</div></body></html>"""

ASSIGNMENT = """<!doctype html><html><head><title>Normalization Exercise</title></head>
<body class="path-mod-assign"><div id="region-main">
<nav aria-label="Breadcrumb"><a href="/my/">Dashboard</a>
<a href="/course/view.php?id=2">Database Systems</a></nav>
<h1>Normalization Exercise</h1>
<div id="intro"><p>Normalize the relation StudentCourse(student_id, course_id, student_name,
course_name, instructor) to 3NF. Explain each normal form and submit a PDF.</p></div>
<div data-region="activity-dates">Due: 12 November 2026, 11:59 PM</div>
<div class="submissionstatustable">Submission status: No attempt</div>
<a href="/pluginfile.php/99/mod_assign/intro/instructions.pdf">instructions.pdf</a>
<a href="/login/logout.php">Log out</a>
</div></body></html>"""


def _make_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (60, 80),
        "Normalization Exercise\n\nRelation: StudentCourse(student_id, course_id,\n"
        "student_name, course_name, instructor)\n\nTask: normalize to 3NF.",
        fontsize=11,
    )
    data = document.tobytes()
    document.close()
    return data


PDF_BYTES = _make_pdf()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/pluginfile.php/99/mod_assign/intro/instructions.pdf":
            body = PDF_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header(
                "Content-Disposition", 'attachment; filename="instructions.pdf"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/course/view.php":
            body = COURSE
        elif path == "/mod/assign/view.php":
            body = ASSIGNMENT
        else:
            body = COURSES
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

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


def test_full_assignment_workflow(tmp_path: Path, mock_moodle: str) -> None:
    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "browser" / "profile",
        moodle_url=None,
    )
    settings.ensure_directories()

    async def scenario() -> None:
        events: list[tuple[str, str]] = []
        session = BrowserSession(
            settings, lambda kind, message, _data: events.append((kind, message))
        )
        db = Database(tmp_path / "database" / "e2e.db")
        approvals: list[tuple[str, dict]] = []

        async def approve(action: str, details: dict) -> bool:
            approvals.append((action, details))
            return False  # exercise the denial path for overwrites

        ctx = ToolContext(
            settings=settings,
            db=db,
            browser=session,
            jail=PathJail(
                {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
            ),
            emit=lambda kind, message, data=None: events.append((kind, message)),
            approve=approve,
        )
        registry = build_registry()

        async def call(name: str, **kwargs):
            spec = registry.get(name)
            assert spec is not None, name
            return await spec.handler(ctx, spec.args_model(**kwargs))

        try:
            await session.start(f"{mock_moodle}/my/courses.php")
            assert session.status.state == BrowserState.READY
            assert session.status.logged_in is True

            courses = await call("list_courses")
            assert courses["count"] == 2
            assert {c["name"] for c in courses["courses"]} == {
                "Database Systems",
                "Software Quality Engineering",
            }

            opened = await call("open_course", course_name="Database Systems")
            assert opened["pending_count"] == 1
            assert opened["assignments"][0]["name"] == "Normalization Exercise"

            assignment = await call(
                "open_assignment", assignment_name="Normalization Exercise"
            )
            assert "3NF" in assignment["instructions"]
            assert assignment["assignment"]["course_name"] == "Database Systems"
            workspace = assignment["workspace"]["workspace"]
            assert workspace == "Database Systems/Normalization Exercise"
            instructions_file = (
                settings.workspace_dir / workspace / "instructions" / "assignment.md"
            )
            assert instructions_file.exists()
            assert "Normalization Exercise" in instructions_file.read_text(encoding="utf-8")

            attachment = assignment["assignment"]["attachments"][0]
            assert attachment["href"].endswith("instructions.pdf")

            downloaded = await call("download_attachment", url=attachment["href"])
            assert downloaded["filename"] == "instructions.pdf"
            saved_pdf = settings.workspace_dir / downloaded["path"]
            assert saved_pdf.read_bytes().startswith(b"%PDF")

            parsed = await call("read_pdf", path=downloaded["path"])
            assert "StudentCourse" in parsed["text"]
            assert "3NF" in parsed["text"]

            # A second download must ask for approval and must not overwrite on denial.
            before = saved_pdf.read_bytes()
            with pytest.raises(ToolError) as denied:
                await call("download_attachment", url=attachment["href"])
            assert "declined" in str(denied.value).lower()
            assert saved_pdf.read_bytes() == before
            assert approvals and approvals[0][0] == "overwrite_file"

            word = await call(
                "create_word_document",
                title="Normalization to 3NF",
                filename="solution.docx",
                content=(
                    "# Solution\n\n## Steps\n"
                    "1. Identify the functional dependencies.\n"
                    "2. Remove partial dependencies for 2NF.\n"
                    "3. Remove transitive dependencies for 3NF.\n\n"
                    "The relation becomes Student, Course, and Enrollment.\n"
                ),
            )
            assert word["path"].endswith("documents/solution.docx")
            assert (settings.workspace_dir / word["path"]).exists()

            pdf = await call(
                "create_pdf",
                title="Normalization summary",
                filename="summary.pdf",
                content="# Summary\n\n- Student(student_id, student_name)\n"
                "- Course(course_id, course_name, instructor)\n"
                "- Enrollment(student_id, course_id)\n",
            )
            assert pdf["pages"] >= 1
            assert (settings.workspace_dir / pdf["path"]).exists()

            code = await call(
                "create_code_file",
                filename="normalize.py",
                language="python",
                code=(
                    "def normalize(rows):\n"
                    "    students = {(r['student_id'], r['student_name']) for r in rows}\n"
                    "    courses = {(r['course_id'], r['course_name'], r['instructor']) for r in rows}\n"
                    "    enrolments = {(r['student_id'], r['course_id']) for r in rows}\n"
                    "    return students, courses, enrolments\n"
                ),
            )
            assert code["path"].endswith("code/normalize.py")
            code_file = settings.workspace_dir / code["path"]
            assert "def normalize" in code_file.read_text(encoding="utf-8")
            assert code["message"].endswith("It was not executed.")

            refreshed = await call(
                "save_assignment_workspace",
                notes="Saved during the end-to-end test.",
                overwrite=False,
            )
            assert refreshed["workspace"]["existing_not_overwritten"]
            assert (settings.workspace_dir / refreshed["workspace"]["workspace"]).exists()

            detail_snapshot = await take_snapshot(session.page)
            detail = parse_assignment_detail(detail_snapshot)
            assert detail["name"] == "Normalization Exercise"

            assert db.list_courses()
            assert db.list_assignments()[0]["name"] == "Normalization Exercise"
            assert any(kind == "file" for kind, _message in events)
            assert any(kind == "download" for kind, _message in events)
        finally:
            await session.close()
            db.close()

    asyncio.run(scenario())
