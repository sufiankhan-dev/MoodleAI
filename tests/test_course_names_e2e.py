"""End-to-end check that a course list showing codes returns full names.

Run with:  $env:RUN_BROWSER_TESTS="1"; python -m pytest tests/test_course_names_e2e.py -q

A visible Chrome window opens against a local mock Moodle whose dashboard only
exposes shortnames (``CS-3301-CS-261-BS-SE-5B``). The real ``list_courses``
handler must read each course page header and return the course fullname.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import load_settings
from app.security import PathJail
from browser.session import BrowserSession, BrowserState
from database.db import Database
from tools import build_registry
from tools.base import ToolContext

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

COURSES_PAGE = """<!doctype html><html><head><title>My courses</title></head>
<body class="path-my"><div id="region-main">
<h1>My courses</h1>
<a href="/course/view.php?id=2">CS-3301-CS-261-BS-SE-5B</a>
<div data-course-id="3"><a href="/course/view.php?id=3"><img alt="" src="/pixel.gif"></a></div>
<a href="/course/view.php?id=4">Operating Systems</a>
<a href="/login/logout.php">Log out</a>
</div></body></html>"""

COURSE_PAGES = {
    "2": """<!doctype html><html><head><title>Course: Database Systems | LMS</title></head>
<body class="path-course-view"><div id="region-main">
<div class="page-header-headings"><h1>Database Systems</h1></div>
<p>Week 4 slides.</p></div></body></html>""",
    "3": """<!doctype html><html><head><title>Discrete Structures | LMS</title></head>
<body class="path-course-view"><div id="region-main"><h1>Discrete Structures</h1></div>
</body></html>""",
    "4": """<!doctype html><html><head><title>Operating Systems | LMS</title></head>
<body class="path-course-view"><div id="region-main"><h1>Operating Systems</h1></div>
</body></html>""",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        body = COURSES_PAGE
        if path == "/course/view.php":
            course_id = self.path.split("id=")[-1].split("&")[0] or "2"
            body = COURSE_PAGES.get(course_id, COURSES_PAGE)
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


class _AuthHandler(_Handler):
    """Same pages, but every request needs HTTP Basic auth (campus-proxy style)."""

    user = "student"
    password = "secret-pass"

    def do_GET(self) -> None:  # noqa: N802
        expected = "Basic " + base64.b64encode(
            f"{self.user}:{self.password}".encode()
        ).decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Enter DSU user name password"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()


@pytest.fixture()
def mock_moodle_protected() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_course_codes_are_replaced_with_full_names(tmp_path: Path, mock_moodle: str) -> None:
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
        db = Database(tmp_path / "database" / "names.db")
        ctx = ToolContext(
            settings=settings,
            db=db,
            browser=session,
            jail=PathJail(
                {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
            ),
            emit=lambda kind, message, data=None: events.append((kind, message)),
        )
        registry = build_registry()

        try:
            await session.start(f"{mock_moodle}/my/courses.php")
            assert session.status.state == BrowserState.READY
            assert session.status.logged_in is True

            spec = registry.get("list_courses")
            assert spec is not None
            result = await spec.handler(ctx, spec.args_model())

            names = {course["name"] for course in result["courses"]}
            assert names == {"Database Systems", "Discrete Structures", "Operating Systems"}
            assert result["count"] == 3

            stored = {row["name"] for row in db.list_courses()}
            assert stored == names

            spec = registry.get("open_course")
            assert spec is not None
            opened = await spec.handler(
                ctx, spec.args_model(course_name="database systems")
            )
            assert opened["course"]["name"] == "Database Systems"
        finally:
            await session.close()
            db.close()

    asyncio.run(scenario())


def test_protected_portal_opens_with_in_memory_credentials(
    tmp_path: Path, mock_moodle_protected: str
) -> None:
    """A campus Basic-auth proxy must not need Chrome's native dialog."""

    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "browser" / "profile",
        moodle_url=None,
    )
    settings.ensure_directories()

    async def scenario() -> None:
        session = BrowserSession(settings, lambda *_args: None)
        db = Database(tmp_path / "database" / "auth.db")
        ctx = ToolContext(
            settings=settings,
            db=db,
            browser=session,
            jail=PathJail(
                {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
            ),
        )
        registry = build_registry()

        try:
            status = await session.start(
                f"{mock_moodle_protected}/my/courses.php",
                http_credentials={
                    "username": _AuthHandler.user,
                    "password": _AuthHandler.password,
                },
            )
            assert status.logged_in is True
            payload = status.to_dict()
            assert "secret-pass" not in str(payload)
            assert "student" not in str(payload)

            spec = registry.get("list_courses")
            assert spec is not None
            result = await spec.handler(ctx, spec.args_model())
            assert {c["name"] for c in result["courses"]} == {
                "Database Systems",
                "Discrete Structures",
                "Operating Systems",
            }
        finally:
            await session.close()
            db.close()

    asyncio.run(scenario())
