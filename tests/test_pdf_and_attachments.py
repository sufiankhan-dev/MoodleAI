"""Phase 7 tests: PDF text extraction and attachment downloads."""

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
from browser.session import BrowserSession
from database.db import Database
from tools.attachment_tools import DownloadArgs, download_attachment, filename_from_disposition
from tools.base import ToolContext, ToolError
from tools.pdf_tools import ReadPdfArgs, read_pdf
from tools.workspace import assignment_workspace


def _make_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def _context(tmp_path: Path, browser=None) -> ToolContext:
    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "profile",
    )
    settings.ensure_directories()
    jail = PathJail(
        {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
    )
    db = Database(tmp_path / "test.db")
    return ToolContext(settings=settings, db=db, browser=browser, jail=jail)


def test_read_pdf_and_save_extracted_text(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    base, relative = assignment_workspace(ctx.jail, "Database Systems", "Normalization Exercise")
    pdf_path = base / "attachments" / "instructions.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(
        _make_pdf("Normalize the relation to 3NF. Submit a PDF.\n" * 50)
    )

    ctx.state["current_assignment"] = {
        "name": "Normalization Exercise",
        "course_name": "Database Systems",
        "workspace": relative,
    }

    result = asyncio.run(
        read_pdf(ctx, ReadPdfArgs(path=f"{relative}/attachments/instructions.pdf"))
    )
    assert result["ok"] is True
    assert "3NF" in result["text"]
    assert result["page_count"] == 1
    assert result["saved_text"]
    assert (base / "extracted" / "instructions.txt").exists()

    short = asyncio.run(
        read_pdf(
            ctx,
            ReadPdfArgs(
                path=f"{relative}/attachments/instructions.pdf",
                max_chars=1000,
                save_text=False,
            ),
        )
    )
    assert short["truncated"] is True
    assert len(short["text"]) <= 1000


def test_read_pdf_rejects_non_pdf(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    target = ctx.settings.workspace_dir / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    with pytest.raises(ToolError):
        asyncio.run(read_pdf(ctx, ReadPdfArgs(path="notes.txt")))


def test_filename_from_disposition() -> None:
    assert filename_from_disposition('attachment; filename="report.pdf"') == "report.pdf"
    assert (
        filename_from_disposition("attachment; filename*=UTF-8''my%20report.pdf")
        == "my report.pdf"
    )
    assert filename_from_disposition(None) is None


pytestmark_browser = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 to launch a visible browser",
)

PDF_BYTES = _make_pdf("Assignment attachment: use 3NF.")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/pluginfile.php"):
            body = PDF_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="week4.pdf"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"""<!doctype html><html><body class="path-mod-assign">
        <div id="region-main"><h1>Normalization Exercise</h1>
        <a href="/pluginfile.php/99/week4.pdf">week4.pdf</a></div></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytestmark_browser
def test_download_attachment_with_approval(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "profile",
        moodle_url=None,
    )
    settings.ensure_directories()
    jail = PathJail(
        {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
    )
    db = Database(tmp_path / "test.db")

    async def scenario() -> None:
        session = BrowserSession(settings)
        approvals: list[str] = []

        async def approve(action, _details):
            approvals.append(action)
            return False  # deny the overwrite on the second download

        ctx = ToolContext(settings=settings, db=db, browser=session, jail=jail, approve=approve)
        try:
            await session.start(f"{base_url}/mod/assign/view.php?id=10")
            ctx.state["current_course"] = {
                "name": "Database Systems",
                "db_id": db.upsert_course("Database Systems", f"{base_url}/course/view.php?id=2"),
            }
            ctx.state["current_assignment"] = {
                "name": "Normalization Exercise",
                "course_name": "Database Systems",
                "workspace": "Database Systems/Normalization Exercise",
            }

            first = await download_attachment(
                ctx, DownloadArgs(url=f"{base_url}/pluginfile.php/99/week4.pdf")
            )
            assert first["ok"] is True
            assert first["filename"] == "week4.pdf"
            target = settings.workspace_dir / first["path"]
            assert target.exists()
            assert target.read_bytes().startswith(b"%PDF")

            with pytest.raises(ToolError):
                await download_attachment(
                    ctx, DownloadArgs(url=f"{base_url}/pluginfile.php/99/week4.pdf")
                )
            assert approvals == ["overwrite_file"]
        finally:
            await session.close()
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
