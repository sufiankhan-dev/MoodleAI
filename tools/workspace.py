"""Assignment workspace layout and safe writes.

Each assignment gets its own folder with a stable set of subfolders, so the
student can find originals, extracted text, AI explanations, generated code,
documents, PDFs, and final submission files separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security import PathJail, sanitize_component

ASSIGNMENT_SUBDIRS = (
    "instructions",
    "attachments",
    "extracted",
    "explanations",
    "code",
    "documents",
    "pdfs",
    "submission",
)


def assignment_workspace(
    jail: PathJail,
    course_name: str,
    assignment_name: str,
) -> tuple[Path, str]:
    """Create the assignment tree and return (absolute path, relative path)."""
    course = sanitize_component(course_name or "Uncategorised course", fallback="Course")
    assignment = sanitize_component(assignment_name or "Assignment", fallback="Assignment")
    relative = f"{course}/{assignment}"
    base = jail.resolve("workspace", relative, create_parents=True)
    for subdir in ASSIGNMENT_SUBDIRS:
        (base / subdir).mkdir(parents=True, exist_ok=True)
    return base, relative


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_assignment_bundle(
    jail: PathJail,
    *,
    course_name: str,
    assignment_name: str,
    instructions: str = "",
    full_text: str = "",
    metadata: dict[str, Any] | None = None,
    overwrite: bool = False,
    max_full_text: int = 60000,
) -> dict[str, Any]:
    """Persist instructions, metadata, and page text without silent overwrites."""
    base, relative = assignment_workspace(jail, course_name, assignment_name)
    meta = dict(metadata or {})
    meta.update(
        {
            "course": course_name,
            "assignment": assignment_name,
            "saved_at": _utc_now(),
        }
    )

    instructions_path = base / "instructions" / "assignment.md"
    metadata_path = base / "instructions" / "metadata.json"
    page_text_path = base / "extracted" / "page.txt"

    body = (instructions or "").strip()
    markdown = (
        f"# {assignment_name}\n\n"
        f"- Course: {course_name}\n"
        f"- Moodle URL: {meta.get('url', '')}\n"
        f"- Due (as shown in Moodle): {meta.get('due_text', '')}\n"
        f"- Saved: {meta['saved_at']}\n\n"
        "## Instructions (extracted from Moodle)\n\n"
        f"{body or '_No separate instruction block was found. See the full page text._'}\n\n"
        "## Full page text (untrusted source, for reference)\n\n"
        f"{(full_text or '').strip()[:max_full_text]}\n"
    )

    created: list[str] = []
    existing: list[str] = []

    for path, content in (
        (instructions_path, markdown),
        (metadata_path, json.dumps(meta, ensure_ascii=False, indent=2)),
        (page_text_path, (full_text or "").strip()[:max_full_text]),
    ):
        label = path.relative_to(base).as_posix()
        if path.exists() and not overwrite:
            existing.append(label)
            continue
        if path.exists() and overwrite:
            _write_atomic(path, content)
            existing.append(label)
        else:
            _write_atomic(path, content)
            created.append(label)

    return {
        "workspace": relative,
        "absolute_workspace": str(base),
        "instructions_path": f"{relative}/instructions/assignment.md",
        "metadata_path": f"{relative}/instructions/metadata.json",
        "page_text_path": f"{relative}/extracted/page.txt",
        "created": created,
        "existing_not_overwritten": existing,
        "subfolders": list(ASSIGNMENT_SUBDIRS),
    }
