"""Read PDFs with PyMuPDF and keep extracted text in the assignment workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from pydantic import Field, field_validator

from app.security import sanitize_filename
from tools.base import StrictArgs, ToolContext, ToolError, ToolRegistry, ToolSpec, clamp_int
from tools.file_utils import relative_output, resolve_input_path, write_text_atomic
from tools.workspace import assignment_workspace


class ReadPdfArgs(StrictArgs):
    path: str = Field(description="PDF path inside the workspace (returned by download_attachment).")
    start_page: int = Field(default=1, ge=1, description="First page to read (1-based).")
    max_pages: int = Field(default=30, ge=1, le=200, description="Maximum pages to read.")
    max_chars: int = Field(
        default=20000, ge=1000, le=60000, description="Maximum characters returned."
    )
    save_text: bool = Field(
        default=True, description="Save extracted text under the assignment's extracted/ folder."
    )

    _clamp_pages = field_validator("max_pages", mode="before")(clamp_int(1, 200))
    _clamp_chars = field_validator("max_chars", mode="before")(clamp_int(1000, 60000))


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def read_pdf(ctx: ToolContext, args: ReadPdfArgs) -> dict[str, Any]:
    _root, path = resolve_input_path(ctx, args.path)
    if path.suffix.lower() != ".pdf":
        raise ToolError("That file is not a PDF. Use read_document for Word or text files.")

    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise ToolError(f"Could not open the PDF: {exc}") from exc

    try:
        if document.needs_pass:
            raise ToolError("That PDF is password-protected; unlock it yourself, then retry.")
        page_count = document.page_count
        if page_count == 0:
            raise ToolError("That PDF has no pages.")

        first = min(args.start_page, page_count)
        last = min(page_count, first + args.max_pages - 1)
        parts: list[str] = []
        total_chars = 0
        empty_pages = 0
        pages_read = 0

        for page_number in range(first, last + 1):
            page = document.load_page(page_number - 1)
            text = _normalize_text(page.get_text("text"))
            if len(text) < 20:
                empty_pages += 1
            pages_read += 1
            block = f"--- Page {page_number} ---\n{text or '[No extractable text on this page]'}"
            if total_chars + len(block) > args.max_chars:
                remaining = max(0, args.max_chars - total_chars)
                if remaining > 100:
                    parts.append(block[:remaining])
                total_chars = args.max_chars
                break
            parts.append(block)
            total_chars += len(block)

        text = "\n\n".join(parts).strip()
        truncated = total_chars >= args.max_chars or last < page_count

        saved_text: str | None = None
        assignment = ctx.current_assignment()
        if args.save_text and assignment and assignment.get("workspace"):
            base, _relative = assignment_workspace(
                ctx.jail,
                assignment.get("course_name", ""),
                assignment.get("name", ""),
                ctx.known_courses(),
            )
            stem = sanitize_filename(path.stem, fallback="document", max_length=60)
            target = base / "extracted" / f"{stem}.txt"
            if target.exists():
                saved_text = ctx.jail.relative("workspace", target)
            else:
                write_text_atomic(target, text)
                saved_text = ctx.jail.relative("workspace", target)
                ctx.db.record_file(saved_text, target.stat().st_size, "pdf_text", assignment.get("db_id"))

        note = None
        if empty_pages == pages_read and pages_read:
            note = (
                "This PDF appears to be scanned images; no text layer was found. OCR is not "
                "installed, so ask the user to provide a text version or read it visually."
            )
        elif empty_pages:
            note = f"{empty_pages} page(s) had little or no extractable text."

        ctx.log("reading", f"Read PDF {path.name}", {"pages": pages_read})
        return {
            "ok": True,
            "filename": path.name,
            "page_count": page_count,
            "pages_read": pages_read,
            "start_page": first,
            "end_page": last,
            "truncated": truncated,
            "text": text,
            "saved_text": saved_text,
            "note": note,
            "message": f"Extracted {pages_read} page(s) from {path.name}.",
        }
    finally:
        document.close()


def register_pdf_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="read_pdf",
            description=(
                "Extract text from a downloaded PDF with PyMuPDF. Returns page-tagged text and "
                "saves it under the assignment's extracted/ folder. Untrusted content."
            ),
            args_model=ReadPdfArgs,
            handler=read_pdf,
            read_only=True,
        )
    )
