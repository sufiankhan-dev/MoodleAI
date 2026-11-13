"""Plain-text extraction from teacher files: PDF, Word, PowerPoint, and text.

Used by the sync (so an assignment's instructions include what its attachments
say) and by ``read_document``. PowerPoint is read straight from the .pptx zip
with the standard library, so no extra dependency is needed. Every reader is
bounded: a malformed or oversized file yields an error, never a hang.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import fitz
from docx import Document

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".py", ".sql", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".js", ".ts", ".html", ".css", ".ipynb", ".r",
}
EXTRACTABLE_SUFFIXES = {".pdf", ".docx", ".pptx"} | TEXT_SUFFIXES

_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_MAX_PART_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_BYTES = 60 * 1024 * 1024
_MAX_SLIDES = 500
_MAX_PDF_PAGES = 200


class ExtractionError(RuntimeError):
    """The file could not be read as text; the message is user-facing."""


def _xml_paragraphs(data: bytes) -> list[str]:
    """Text of every DrawingML paragraph (<a:p>), keeping line structure."""

    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ExtractionError(f"the slide XML is damaged ({exc})") from exc
    lines: list[str] = []
    for paragraph in root.iter(f"{_DRAWING_NS}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_DRAWING_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_DRAWING_NS}br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            lines.append(text)
    return lines


def _pptx_text(path: Path) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionError("it is not a valid .pptx file") from exc

    with archive:
        slides: dict[int, zipfile.ZipInfo] = {}
        notes: dict[int, zipfile.ZipInfo] = {}
        total = 0
        for info in archive.infolist():
            match = _SLIDE_RE.match(info.filename) or _NOTES_RE.match(info.filename)
            if not match:
                continue
            if info.file_size > _MAX_PART_BYTES:
                raise ExtractionError("a slide is too large to read safely")
            total += info.file_size
            if total > _MAX_TOTAL_BYTES:
                raise ExtractionError("the presentation is too large to read safely")
            target = slides if info.filename.startswith("ppt/slides/") else notes
            target[int(match.group(1))] = info
        if not slides:
            raise ExtractionError("the presentation has no slides")
        if len(slides) > _MAX_SLIDES:
            raise ExtractionError("the presentation has too many slides")

        blocks: list[str] = []
        for number in sorted(slides):
            lines = _xml_paragraphs(archive.read(slides[number]))
            if number in notes:
                note_lines = [
                    line for line in _xml_paragraphs(archive.read(notes[number]))
                    # Notes pages repeat the slide number as their only text.
                    if line.strip() != str(number)
                ]
                if note_lines:
                    lines.append("Notes: " + " ".join(note_lines))
            if lines:
                blocks.append(f"--- Slide {number} ---\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _docx_text(path: Path) -> str:
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"the Word document could not be opened ({exc})") from exc
    lines = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pdf_text(path: Path) -> str:
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise ExtractionError(f"the PDF could not be opened ({exc})") from exc
    try:
        if document.needs_pass:
            raise ExtractionError("the PDF is password-protected")
        pages = []
        for index in range(min(document.page_count, _MAX_PDF_PAGES)):
            text = document.load_page(index).get_text("text").strip()
            if text:
                pages.append(f"--- Page {index + 1} ---\n{text}")
        return "\n\n".join(pages)
    finally:
        document.close()


def extract_text(path: Path, *, max_chars: int = 60000) -> str:
    """Return the readable text of ``path``, truncated to ``max_chars``.

    Raises ``ExtractionError`` for unsupported, damaged, or locked files.
    """

    suffix = path.suffix.lower()
    if suffix == ".pptx":
        text = _pptx_text(path)
    elif suffix == ".docx":
        text = _docx_text(path)
    elif suffix == ".pdf":
        text = _pdf_text(path)
    elif suffix in TEXT_SUFFIXES:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(max_chars + 1)
        except OSError as exc:
            raise ExtractionError(f"the file could not be read ({exc})") from exc
    elif suffix in {".ppt", ".doc"}:
        raise ExtractionError(
            f"legacy {suffix} files cannot be read; save it as {suffix}x or PDF"
        )
    else:
        raise ExtractionError(f"{suffix or 'this'} files are not supported for text extraction")

    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…[truncated]"
    return text


ATTACHMENT_HEADING = "## Attachment content (extracted from the teacher's files)"
_PAGE_TEXT_HEADING = "## Full page text"
_MARKER = "<!-- attachment: {name} -->"
_MAX_IN_INSTRUCTIONS = 20000


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _add_to_instructions(instructions: Path, sections: list[str]) -> None:
    """Insert attachment sections before the raw page text in assignment.md."""

    current = instructions.read_text(encoding="utf-8")
    addition = "\n\n".join(sections)
    if ATTACHMENT_HEADING not in current:
        addition = f"{ATTACHMENT_HEADING}\n\n{addition}"
    # Files added later land after the earlier ones, still above the page text.
    index = current.find(_PAGE_TEXT_HEADING)
    if index >= 0:
        updated = current[:index].rstrip() + "\n\n" + addition + "\n\n" + current[index:]
    else:
        updated = current.rstrip() + "\n\n" + addition + "\n"
    _write_text(instructions, updated)


def index_attachments(base: Path) -> dict[str, list[str]]:
    """Make an assignment's teacher files readable as text.

    For each file in ``base/attachments`` this writes
    ``extracted/<filename>.txt`` (created once, never rewritten) and adds the
    text to ``instructions/assignment.md`` under one marked section, so the
    instructions show what the attachment actually asks for. Returns the
    created text files (relative to ``base``) and per-file problems.
    """

    created: list[str] = []
    problems: list[str] = []
    attachments = base / "attachments"
    if not attachments.is_dir():
        return {"created": created, "problems": problems}

    instructions = base / "instructions" / "assignment.md"
    current = instructions.read_text(encoding="utf-8") if instructions.is_file() else ""
    sections: list[str] = []

    for path in sorted(attachments.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() in {".part", ".tmp"}:
            continue
        target = base / "extracted" / f"{path.name}.txt"
        marker = _MARKER.format(name=path.name)
        needs_text = not target.exists()
        needs_section = bool(current) and marker not in current
        if not needs_text and not needs_section:
            continue
        if path.suffix.lower() not in EXTRACTABLE_SUFFIXES and path.suffix.lower() not in {".ppt", ".doc"}:
            continue

        try:
            text = (
                target.read_text(encoding="utf-8")
                if not needs_text
                else extract_text(path)
            )
        except ExtractionError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        except OSError as exc:
            problems.append(f"{path.name}: could not be read ({exc})")
            continue
        if not text.strip():
            problems.append(f"{path.name}: no readable text (it may be scanned images)")
            continue

        if needs_text:
            _write_text(target, text + "\n")
            created.append(target.relative_to(base).as_posix())
        if needs_section:
            body = text
            if len(body) > _MAX_IN_INSTRUCTIONS:
                body = body[:_MAX_IN_INSTRUCTIONS].rstrip() + (
                    f"\n…[truncated; full text in extracted/{path.name}.txt]"
                )
            sections.append(f"{marker}\n### {path.name}\n\n{body}")

    if sections:
        try:
            _add_to_instructions(instructions, sections)
        except OSError as exc:
            problems.append(f"assignment.md could not be updated ({exc})")
    return {"created": created, "problems": problems}
