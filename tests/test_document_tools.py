"""Phase 8 tests: Word, PDF, code generation, and document reading."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from app.config import load_settings
from app.security import PathJail
from database.db import Database
from tools.base import ToolContext, ToolError
from tools.document_tools import (
    CreateCodeArgs,
    CreatePdfArgs,
    CreateWordArgs,
    ReadDocumentArgs,
    create_code_file,
    create_pdf,
    create_word_document,
    read_document,
)


def _context(tmp_path: Path) -> ToolContext:
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
    ctx = ToolContext(settings=settings, db=db, browser=None, jail=jail)
    ctx.state["current_assignment"] = {
        "name": "Normalization Exercise",
        "course_name": "Database Systems",
        "workspace": "Database Systems/Normalization Exercise",
    }
    return ctx


def test_create_word_document_and_read_it(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    result = asyncio.run(
        create_word_document(
            ctx,
            CreateWordArgs(
                title="Normalization Report",
                content=(
                    "# Introduction\n"
                    "This report explains **third normal form**.\n\n"
                    "## Tasks\n"
                    "- Identify functional dependencies\n"
                    "- Decompose the relation\n\n"
                    "```sql\nSELECT * FROM students;\n```\n"
                ),
                filename="normalization.docx",
            ),
        )
    )
    assert result["ok"] is True
    assert result["path"].endswith("documents/normalization.docx")
    target = ctx.settings.workspace_dir / result["path"]
    assert target.exists()

    read = asyncio.run(
        read_document(
            ctx,
            ReadDocumentArgs(path=result["path"], save_text=False),
        )
    )
    assert "Normalization Report" in read["text"]
    assert "third normal form" in read["text"]


def test_create_pdf_renders_and_opens(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    result = asyncio.run(
        create_pdf(
            ctx,
            CreatePdfArgs(
                title="SRS Summary",
                content="## Scope\n- Student records\n- Course enrolment\n\nUse `3NF` throughout.",
                filename="srs.pdf",
            ),
        )
    )
    assert result["ok"] is True
    assert result["pages"] >= 1
    target = ctx.settings.workspace_dir / result["path"]
    document = fitz.open(str(target))
    try:
        text = document.load_page(0).get_text("text")
    finally:
        document.close()
    assert "SRS Summary" in text
    assert "Student records" in text


def test_create_code_file_and_reject_executable(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    result = asyncio.run(
        create_code_file(
            ctx,
            CreateCodeArgs(filename="solution.py", code="print('hello')\n"),
        )
    )
    assert result["ok"] is True
    assert (ctx.settings.workspace_dir / result["path"]).read_text(encoding="utf-8").startswith(
        "print"
    )

    with pytest.raises(ToolError):
        asyncio.run(
            create_code_file(ctx, CreateCodeArgs(filename="malware.exe", code="MZ"))
        )


def test_overwrite_requires_approval(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    first = asyncio.run(
        create_code_file(ctx, CreateCodeArgs(filename="query.sql", code="SELECT 1;"))
    )
    assert first["ok"] is True

    async def deny(_action, _details):
        return False

    ctx.approve = deny
    with pytest.raises(ToolError):
        asyncio.run(
            create_code_file(
                ctx, CreateCodeArgs(filename="query.sql", code="SELECT 2;", overwrite=True)
            )
        )

    async def allow(_action, _details):
        return True

    ctx.approve = allow
    second = asyncio.run(
        create_code_file(
            ctx, CreateCodeArgs(filename="query.sql", code="SELECT 2;", overwrite=True)
        )
    )
    assert second["ok"] is True
    assert "SELECT 2;" in (ctx.settings.workspace_dir / second["path"]).read_text(
        encoding="utf-8"
    )


def test_files_follow_the_synced_course_folder(tmp_path: Path) -> None:
    """A model-invented or short course name must not create a second folder."""

    ctx = _context(tmp_path)
    ctx.db.upsert_course(
        "CS301 Database Systems (Fall 2026)",
        "https://lms.example.edu/course/view.php?id=2",
        "2",
    )
    ctx.state["current_assignment"]["course_name"] = "database systems"
    result = asyncio.run(
        create_code_file(ctx, CreateCodeArgs(filename="schema.sql", code="SELECT 1;"))
    )
    assert result["path"] == (
        "CS301 Database Systems (Fall 2026)/Normalization Exercise/code/schema.sql"
    )


def test_course_open_without_assignment_saves_into_the_course(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    ctx.state.pop("current_assignment")
    ctx.state["current_course"] = {"name": "Database Systems"}
    result = asyncio.run(
        create_code_file(ctx, CreateCodeArgs(filename="notes.sql", code="SELECT 1;"))
    )
    assert result["path"] == "Database Systems/code/notes.sql"
    assert (ctx.settings.workspace_dir / result["path"]).exists()

    ctx.state.pop("current_course")
    loose = asyncio.run(
        create_code_file(ctx, CreateCodeArgs(filename="loose.sql", code="SELECT 1;"))
    )
    assert loose["path"] == "code/loose.sql"
    assert (ctx.settings.generated_dir / loose["path"]).exists()
