"""Phase 4 test: launch visible Chrome, detect login states, enforce host policy.

Run with:  $env:RUN_BROWSER_TESTS="1"; python -m pytest tests/test_browser_session.py -q
A real Chrome window will open and close during the test.
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
from browser.session import BrowserError, BrowserSession, BrowserState

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

LOGIN_HTML = b"""<!doctype html><html><head><title>Log in to Moodle</title></head>
<body><h1>Log in</h1><form method="post" action="/login/index.php">
<input name="username" type="text"><input name="password" type="password">
<button type="submit">Log in</button></form></body></html>"""

DASHBOARD_HTML = b"""<!doctype html><html><head><title>Dashboard</title></head>
<body><nav><button id="user-menu-toggle">Student Name</button>
<a href="/login/logout.php">Log out</a></nav>
<h1>My courses</h1><a href="/course/view.php?id=2">Database Systems</a></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/login/"):
            body = LOGIN_HTML
        elif self.path.startswith("/my/"):
            body = DASHBOARD_HTML
        else:
            body = LOGIN_HTML
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


def test_visible_browser_login_detection(tmp_path: Path, mock_moodle: str) -> None:
    settings = replace(
        load_settings(),
        browser_profile_dir=tmp_path / "profile",
        moodle_url=None,
    )

    async def scenario() -> None:
        events: list[tuple[str, str]] = []
        session = BrowserSession(settings, lambda kind, message, _data: events.append((kind, message)))
        try:
            status = await session.start(f"{mock_moodle}/login/index.php")
            assert status.state in {BrowserState.LOGIN_REQUIRED, BrowserState.UNKNOWN}
            assert status.logged_in is False

            await session.navigate(f"{mock_moodle}/my/")
            status = await session.refresh_status()
            assert status.state == BrowserState.READY
            assert status.logged_in is True
            assert status.url.startswith("http://127.0.0.1")

            page = await session.current_page()
            assert page["title"] == "Dashboard"

            with pytest.raises(BrowserError):
                await session.navigate("https://example.com/")
        finally:
            await session.close()
            assert not session.is_running

    asyncio.run(scenario())
