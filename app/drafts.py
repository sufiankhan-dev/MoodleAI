"""Assignment drafts: what the agent produced for an assignment, and its review state.

A draft is the set of files the agent generated in an assignment's workspace
(``code/``, ``documents/``, ``pdfs/``, ``submission/``). Teacher files
(``attachments/``) and the app's own notes (``instructions/``, ``extracted/``,
``explanations/``) are never part of it.

When the user marks a draft reviewed, the SHA-256 of every draft file is
written to ``<assignment>/review.json``. Any later edit, addition, or removal
makes the draft read as "changed" until it is reviewed again. Nothing here
talks to Moodle; submitting stays a manual step for the user.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.security import PathJail, PathSecurityError

logger = logging.getLogger(__name__)

DRAFT_DIRS = ("code", "documents", "pdfs", "submission")
INPUT_DIRS = ("instructions", "attachments", "extracted")
REVIEW_FILE = "review.json"
REVIEW_NOTE = "explanations/REVIEW.md"
_INPUT_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".csv", ".json", ".py", ".sql", ".java", ".c", ".cpp"}
_SKIP_SUFFIXES = {".tmp", ".part"}
_MAX_FILES = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(base: Path, folders: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for folder in folders:
        root = base / folder
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.rglob("*")):
            if len(found) >= _MAX_FILES:
                return found
            if path.is_symlink() or not path.is_file():
                continue
            if path.name == ".gitkeep" or path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            found.append(path)
    return found


def resolve_assignment_dir(jail: PathJail, workspace_path: str | None) -> Path | None:
    """Return the assignment folder inside the jail, or None if it is unknown."""

    if not workspace_path:
        return None
    try:
        base = jail.resolve("workspace", workspace_path)
    except PathSecurityError:
        logger.warning("Assignment workspace path rejected by the jail")
        return None
    return base if base.is_dir() else None


def input_files(jail: PathJail, base: Path) -> list[str]:
    """Workspace-relative paths of the instructions and teacher files to read.

    Text already inside ``assignment.md`` is not listed twice: the Moodle page
    text (``extracted/page.txt``) always is, and an attachment's
    ``extracted/<name>.txt`` is when its full text was folded in. Every extra
    read costs a slow CPU model round.
    """

    instructions = base / "instructions" / "assignment.md"
    try:
        folded = instructions.read_text(encoding="utf-8") if instructions.is_file() else ""
    except OSError:
        folded = ""

    paths = []
    for path in _iter_files(base, INPUT_DIRS):
        if path.suffix.lower() not in _INPUT_SUFFIXES:
            continue
        if path.name == "metadata.json":
            continue
        if folded and path.parent.name == "extracted":
            if path.name == "page.txt":
                continue
            if path.name.endswith(".txt"):
                source = path.name[: -len(".txt")]
                if (
                    f"<!-- attachment: {source} -->" in folded
                    and f"full text in extracted/{path.name}" not in folded
                ):
                    continue
        paths.append(jail.relative("workspace", path))
    return paths[:40]


def draft_files(jail: PathJail, base: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in _iter_files(base, DRAFT_DIRS):
        relative = jail.relative("workspace", path)
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError:
            logger.debug("Could not hash draft file", exc_info=True)
            continue
        files.append(
            {
                "name": path.name,
                "path": relative,
                "folder": path.relative_to(base).parts[0],
                "size": size,
                "sha256": digest,
                "download_url": f"/api/files/download?root=workspace&path={quote(relative)}",
            }
        )
    return files


def load_review(base: Path) -> dict[str, Any] | None:
    path = base / REVIEW_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Unreadable review record", exc_info=True)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def draft_summary(jail: PathJail, base: Path | None) -> dict[str, Any]:
    """Describe the draft: its files, review note, and review state.

    ``status`` is ``none`` (nothing generated), ``draft`` (never reviewed),
    ``reviewed`` (every file matches the recorded hashes) or ``changed``.
    """

    if base is None:
        return {
            "status": "none",
            "workspace": None,
            "files": [],
            "inputs": [],
            "review_note": None,
            "reviewed_at": None,
            "changed": [],
        }

    files = draft_files(jail, base)
    review = load_review(base)
    note = base / REVIEW_NOTE
    current = {item["path"]: item["sha256"] for item in files}

    changed: list[str] = []
    if review:
        recorded: dict[str, str] = {
            str(key): str(value) for key, value in review["files"].items()
        }
        for path, digest in current.items():
            if recorded.get(path) != digest:
                changed.append(path)
        changed.extend(path for path in recorded if path not in current)
        for item in files:
            item["changed"] = item["path"] in changed

    if not files:
        status = "none"
    elif review is None:
        status = "draft"
    elif changed:
        status = "changed"
    else:
        status = "reviewed"

    return {
        "status": status,
        "workspace": jail.relative("workspace", base),
        "files": files,
        "inputs": input_files(jail, base),
        "review_note": jail.relative("workspace", note) if note.is_file() else None,
        "reviewed_at": review.get("reviewed_at") if review else None,
        "changed": sorted(changed),
    }


def record_review(jail: PathJail, base: Path) -> dict[str, Any]:
    """Freeze the current draft by writing every file's hash to review.json."""

    files = draft_files(jail, base)
    if not files:
        raise ValueError("There are no draft files to review yet. Draft the assignment first.")
    record = {
        "reviewed_at": _utc_now(),
        "files": {item["path"]: item["sha256"] for item in files},
    }
    path = base / REVIEW_FILE
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temp.replace(path)
    return draft_summary(jail, base)


def write_review_note(base: Path, assignment_name: str, content: str) -> Path:
    """Save the agent's final checklist as explanations/REVIEW.md."""

    path = base / REVIEW_NOTE
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Review: {assignment_name}\n\n"
        f"_Written by the local model on {_utc_now()}. Check every file before you "
        "submit; this draft has not been verified._\n\n"
        f"{content.strip()}\n"
    )
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(body, encoding="utf-8")
    temp.replace(path)
    return path


def build_draft_request(row: dict[str, Any], inputs: list[str], workspace: str) -> str:
    """The user turn that starts a draft run. Kept short; files carry the detail."""

    lines = [
        f'Draft my assignment "{row.get("name") or "Assignment"}"'
        + (f' for the course "{row["course_name"]}"' if row.get("course_name") else "")
        + ".",
        f"Assignment folder: {workspace}",
    ]
    if row.get("due_text"):
        lines.append(f"Due (as Moodle shows it): {row['due_text']}")
    if inputs:
        lines.append("Saved instruction and teacher files to read first:")
        lines.extend(f"- {path}" for path in inputs)
        lines.append(
            "The teacher's attachments are already downloaded and their text is included "
            "in these files. Do not open Moodle or download anything; read these, then "
            "create the deliverables."
        )
    else:
        lines.append(
            "No instructions are saved yet. Open the assignment in Moodle to read them."
        )
    return "\n".join(lines)
