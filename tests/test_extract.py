"""Attachment text extraction: .pptx/.docx/.pdf to text, and assignment.md indexing."""

from __future__ import annotations

import asyncio
import zipfile
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from docx import Document

from app.config import load_settings
from app.security import PathJail
from database.db import Database
from tools.base import ToolContext
from tools.document_tools import ReadDocumentArgs, read_document
from tools.extract import ATTACHMENT_HEADING, ExtractionError, extract_text, index_attachments

_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)


def _slide(*paragraphs: str) -> str:
    body = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in paragraphs)
    return f'<?xml version="1.0"?><p:sld {_NS}><p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>'


def make_pptx(path: Path, slides: list[list[str]], notes: dict[int, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        # Written out of order on purpose: slide10 must sort after slide2.
        for number in reversed(range(1, len(slides) + 1)):
            archive.writestr(f"ppt/slides/slide{number}.xml", _slide(*slides[number - 1]))
        for number, text in (notes or {}).items():
            archive.writestr(f"ppt/notesSlides/notesSlide{number}.xml", _slide(text, str(number)))
    return path


def test_pptx_text_keeps_slide_order_lines_and_notes(tmp_path: Path) -> None:
    slides = [[f"Task {n}", f"Step for {n}"] for n in range(1, 11)]
    path = make_pptx(tmp_path / "lab.pptx", slides, notes={2: "Remember to cast input"})

    text = extract_text(path)
    assert text.index("--- Slide 2 ---") < text.index("--- Slide 10 ---")
    assert "Task 1\nStep for 1" in text
    assert "Notes: Remember to cast input" in text
    # The notes page's own slide number is not repeated as content.
    assert "Notes: Remember to cast input 2" not in text


def test_extract_text_handles_docx_pdf_and_rejects_bad_files(tmp_path: Path) -> None:
    docx_path = tmp_path / "brief.docx"
    document = Document()
    document.add_paragraph("Write a report on sorting.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Marks"
    table.rows[0].cells[1].text = "10"
    document.save(str(docx_path))
    assert extract_text(docx_path) == "Write a report on sorting.\nMarks | 10"

    pdf_path = tmp_path / "brief.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Question 1: explain recursion")
    pdf.save(str(pdf_path))
    pdf.close()
    assert "Question 1: explain recursion" in extract_text(pdf_path)

    broken = tmp_path / "broken.pptx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError):
        extract_text(broken)
    legacy = tmp_path / "old.ppt"
    legacy.write_bytes(b"\xd0\xcf")
    with pytest.raises(ExtractionError, match="legacy"):
        extract_text(legacy)
    assert extract_text(make_pptx(tmp_path / "long.pptx", [["x" * 500]]), max_chars=100).endswith(
        "[truncated]"
    )


def _assignment(tmp_path: Path) -> Path:
    base = tmp_path / "Lab_Task_1"
    (base / "instructions").mkdir(parents=True)
    (base / "attachments").mkdir()
    (base / "extracted").mkdir()
    (base / "instructions" / "assignment.md").write_text(
        "# Lab_Task_1\n\n## Instructions (extracted from Moodle)\n\nPython_Lab_Tasks.pptx\n\n"
        "## Full page text (untrusted source, for reference)\n\nSubmission status No attempt\n",
        encoding="utf-8",
    )
    return base


def test_index_attachments_adds_text_once_above_page_text(tmp_path: Path) -> None:
    base = _assignment(tmp_path)
    make_pptx(base / "attachments" / "Python_Lab_Tasks.pptx", [["Student Profile Creator"]])
    (base / "attachments" / "old.ppt").write_bytes(b"\xd0\xcf")

    result = index_attachments(base)
    assert result["created"] == ["extracted/Python_Lab_Tasks.pptx.txt"]
    assert any("old.ppt" in problem and "legacy" in problem for problem in result["problems"])
    extracted = (base / "extracted" / "Python_Lab_Tasks.pptx.txt").read_text(encoding="utf-8")
    assert "Student Profile Creator" in extracted

    markdown = (base / "instructions" / "assignment.md").read_text(encoding="utf-8")
    assert markdown.index(ATTACHMENT_HEADING) < markdown.index("## Full page text")
    assert markdown.index("Student Profile Creator") < markdown.index("## Full page text")

    # A repeat run changes nothing.
    again = index_attachments(base)
    assert again["created"] == []
    assert (base / "instructions" / "assignment.md").read_text(encoding="utf-8") == markdown

    # A file the teacher adds later joins the same section, above the page text.
    make_pptx(base / "attachments" / "Extra.pptx", [["Bonus task"]])
    index_attachments(base)
    updated = (base / "instructions" / "assignment.md").read_text(encoding="utf-8")
    assert updated.count(ATTACHMENT_HEADING) == 1
    assert updated.index("Bonus task") < updated.index("## Full page text")
    assert updated.index("Student Profile Creator") < updated.index("Bonus task")


def test_read_document_reads_pptx(tmp_path: Path) -> None:
    settings = replace(load_settings(), project_root=tmp_path)
    settings.ensure_directories()
    jail = PathJail({"workspace": settings.workspace_dir, "generated": settings.generated_dir})
    make_pptx(settings.workspace_dir / "lab.pptx", [["Simple Shopping Bill", "Use list indexing"]])
    db = Database(tmp_path / "test.db")
    try:
        ctx = ToolContext(settings=settings, db=db, browser=None, jail=jail)  # type: ignore[arg-type]
        result = asyncio.run(read_document(ctx, ReadDocumentArgs(path="lab.pptx")))
    finally:
        db.close()
    assert result["ok"] is True
    assert "Simple Shopping Bill" in result["text"]
    assert "Use list indexing" in result["text"]
