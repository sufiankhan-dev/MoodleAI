"""Automatic Moodle course + assignment sync for the dashboard.

The app runs this on its own initiative (after the user logs in, or from the
Sync button); the model never triggers it, so the tool registry stays the
model's only surface. Everything here is read-only against Moodle: it reads the
course list, each course's assignment index, each assignment page, and the
teacher's files attached to those pages, and nothing else. Pages are read with
in-page ``fetch`` so the user's visible tab is never navigated.

Locally it writes database rows and, under ``workspace/``, one folder per
course with one folder per assignment (instructions, metadata, page text,
attachments). It only ever creates files: an existing file is never
overwritten, so the approval gate (which guards overwrites) is never needed.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.security import (
    PathJail,
    UrlSecurityError,
    sanitize_filename,
    validate_http_url,
)
from browser.moodle import (
    STATUS_LABELS,
    STATUS_UNKNOWN,
    MoodleLoginLost,
    MoodleParseError,
    fetch_assign_deadlines,
    fetch_assignment_detail,
    fetch_assignment_index,
    is_pending,
    map_assignment_index,
    normalize_submission_status,
)
from browser.session import BrowserError, BrowserSession
from database.db import Database
from tools.attachment_tools import _filename_from_url, filename_from_disposition
from tools.base import ToolContext, ToolError
from tools.extract import index_attachments
from tools.file_utils import write_bytes_atomic
from tools.workspace import assignment_workspace, course_folder, save_assignment_bundle

# Private on purpose: the dashboard reuses the exact discovery the model uses
# (five fallback pages, fullname resolution) instead of a second implementation.
from tools.browser_tools import _load_courses

logger = logging.getLogger(__name__)

URGENCY_OVERDUE = "overdue"
URGENCY_URGENT = "urgent"
URGENCY_SOON = "soon"
URGENCY_UPCOMING = "upcoming"
URGENCY_NONE = "no_due_date"

#: Lower sorts first in the dashboard: what needs attention now, then the rest.
URGENCY_RANK = {
    URGENCY_OVERDUE: 0,
    URGENCY_URGENT: 1,
    URGENCY_SOON: 2,
    URGENCY_UPCOMING: 3,
    URGENCY_NONE: 4,
}

_PER_COURSE_TIMEOUT_S = 20.0
_DEADLINES_TIMEOUT_S = 30.0
_PER_ASSIGNMENT_TIMEOUT_S = 20.0
_PER_FILE_TIMEOUT_S = 60.0

#: The bundle file whose presence means "this assignment was already saved".
_BUNDLE_MARKER = Path("instructions") / "assignment.md"


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def due_urgency(
    due_at: str | None,
    *,
    soon_hours: int,
    urgent_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify a stored due instant for the dashboard badge.

    Computed here rather than in ``app.js`` so the boundaries are unit-testable
    and a browser clock cannot disagree with the stored timestamp.
    """

    moment = _parse_iso(due_at)
    if moment is None:
        return {"urgency": URGENCY_NONE, "due_in_hours": None}
    reference = now or datetime.now(timezone.utc)
    hours = (moment - reference).total_seconds() / 3600.0
    if hours < 0:
        urgency = URGENCY_OVERDUE
    elif hours <= urgent_hours:
        urgency = URGENCY_URGENT
    elif hours <= soon_hours:
        urgency = URGENCY_SOON
    else:
        urgency = URGENCY_UPCOMING
    return {"urgency": urgency, "due_in_hours": round(hours, 1)}


def assignment_view(
    row: dict[str, Any],
    *,
    soon_hours: int,
    urgent_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add the status/urgency fields the dashboard renders to a stored row."""

    stored = str(row.get("status") or "").strip().lower()
    status = (
        stored
        if stored and stored != STATUS_UNKNOWN
        else normalize_submission_status(str(row.get("status_text") or ""))
    )
    timing = due_urgency(
        row.get("due_at"),
        soon_hours=soon_hours,
        urgent_hours=urgent_hours,
        now=now,
    )
    return {
        **row,
        "status": status,
        # An unrecognised phrase (theme or language pack) is shown verbatim
        # rather than hidden behind "Status unknown".
        "status_label": (
            STATUS_LABELS[status]
            if status in STATUS_LABELS and status != STATUS_UNKNOWN
            else str(row.get("status_text") or "").strip() or STATUS_LABELS[STATUS_UNKNOWN]
        ),
        "pending": is_pending(status),
        **timing,
    }


def _empty_summary(started_at: datetime) -> dict[str, Any]:
    return {
        "ok": False,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": None,
        "courses": 0,
        "courses_total": 0,
        "assignments": 0,
        "pending": 0,
        "deadlines": 0,
        "folders": 0,
        "saved": 0,
        "attachments": 0,
        "extracted": 0,
        "deadlines_limited": False,
        "errors": [],
        "notes": [],
    }


async def _download_attachment(
    *,
    browser: BrowserSession,
    settings: Settings,
    jail: PathJail,
    db: Database,
    url: str,
    link_text: str,
    target_dir: Path,
    assignment_id: int | None,
) -> str | None:
    """Fetch one teacher file into ``target_dir``; never overwrite.

    Returns the saved workspace-relative path, or ``None`` when the file was
    already there. Raises ``MoodleParseError`` with a user-facing reason.
    """

    try:
        url = validate_http_url(url, allow_loopback=True)
    except UrlSecurityError as exc:
        raise MoodleParseError(f"blocked link ({exc})") from exc
    if not browser.policy.permits(url):
        raise MoodleParseError("file is not hosted on your Moodle site")

    # pluginfile URLs end in the real filename, so a repeat sync can skip the
    # download entirely instead of fetching bytes it will not write.
    guessed = _filename_from_url(url)
    if guessed != "attachment":
        if (target_dir / sanitize_filename(guessed, fallback="attachment")).exists():
            return None

    try:
        response = await browser.page.request.get(url, timeout=settings.browser_timeout_ms)
    except Exception as exc:
        raise MoodleParseError(f"download failed ({exc})") from exc
    if response.status >= 400:
        raise MoodleParseError(f"Moodle returned HTTP {response.status}")
    headers = response.headers
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        raise MoodleParseError("Moodle returned a page instead of a file")
    length = headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_download_bytes:
        raise MoodleParseError(f"larger than the {settings.max_download_mb} MB limit")
    body = await response.body()
    if len(body) > settings.max_download_bytes:
        raise MoodleParseError(f"larger than the {settings.max_download_mb} MB limit")

    suggested = (
        filename_from_disposition(headers.get("content-disposition"))
        or (guessed if guessed != "attachment" else "")
        or link_text
        or "attachment"
    )
    filename = sanitize_filename(suggested, fallback="attachment")
    if "." not in filename:
        extension = mimetypes.guess_extension(content_type or "")
        if extension:
            filename += extension
    target = target_dir / filename
    if target.exists():
        return None
    write_bytes_atomic(target, body)
    relative = jail.relative("workspace", target)
    db.record_file(relative, len(body), "attachment", assignment_id)
    return relative


async def _save_assignment_files(
    *,
    browser: BrowserSession,
    db: Database,
    settings: Settings,
    jail: PathJail,
    course_name: str,
    course_id: str,
    row: dict[str, Any],
    assignment_id: int,
    summary: dict[str, Any],
) -> None:
    """Save one assignment's instructions and teacher files into its folder.

    New assignments are always read. Already-saved ones are re-read only while
    still pending, to pick up files a teacher added later. Nothing that exists
    is rewritten.
    """

    base, relative = assignment_workspace(jail, course_name, row["name"])
    already_saved = (base / _BUNDLE_MARKER).exists()
    if already_saved and not row.get("pending"):
        db.set_assignment_workspace(assignment_id, relative)
        # Local only: older folders get their attachment text on the next sync.
        _index_attachments(jail, db, base, row["name"], assignment_id, summary)
        return

    detail = await asyncio.wait_for(
        fetch_assignment_detail(browser.page, row["url"], max_chars=settings.max_page_chars),
        timeout=_PER_ASSIGNMENT_TIMEOUT_S,
    )

    if not already_saved:
        bundle = save_assignment_bundle(
            jail,
            course_name=course_name,
            assignment_name=row["name"],
            instructions=detail.get("instructions", ""),
            full_text=detail.get("full_text", ""),
            metadata={
                "url": row["url"],
                "due_text": row.get("due_text") or detail.get("due_text", ""),
                "due_at": row.get("due_at") or "",
                "submission_status_text": row.get("status_text")
                or detail.get("submission_status_text", ""),
                "attachments": detail.get("attachments", []),
                "course_id": course_id,
                "activity_id": row.get("activity_id"),
                "source": "sync",
            },
        )
        for label in ("instructions_path", "metadata_path", "page_text_path"):
            path = bundle[label]
            if path.split("/", 2)[-1] in bundle["created"]:
                absolute = jail.resolve("workspace", path, must_exist=True)
                db.record_file(path, absolute.stat().st_size, "workspace", assignment_id)
        summary["saved"] += 1
    db.set_assignment_workspace(assignment_id, relative)

    for link in detail.get("attachments", []):
        try:
            saved = await asyncio.wait_for(
                _download_attachment(
                    browser=browser,
                    settings=settings,
                    jail=jail,
                    db=db,
                    url=link["href"],
                    link_text=link.get("text", ""),
                    target_dir=base / "attachments",
                    assignment_id=assignment_id,
                ),
                timeout=_PER_FILE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            summary["notes"].append(f"{row['name']}: a file took too long to download.")
            continue
        except MoodleParseError as exc:
            label = link.get("text") or "attachment"
            summary["notes"].append(f"{row['name']}: {label} not saved ({exc}).")
            continue
        if saved:
            summary["attachments"] += 1

    _index_attachments(jail, db, base, row["name"], assignment_id, summary)


def _index_attachments(
    jail: PathJail,
    db: Database,
    base: Path,
    assignment_name: str,
    assignment_id: int,
    summary: dict[str, Any],
) -> None:
    """Extract teacher files to text and fold them into assignment.md."""

    try:
        result = index_attachments(base)
    except Exception as exc:
        logger.exception("Attachment text extraction failed")
        summary["notes"].append(f"{assignment_name}: attachment text not extracted ({exc}).")
        return
    for created in result["created"]:
        absolute = base / created
        db.record_file(
            jail.relative("workspace", absolute), absolute.stat().st_size, "extracted", assignment_id
        )
    summary["extracted"] = summary.get("extracted", 0) + len(result["created"])
    for problem in result["problems"]:
        summary["notes"].append(f"{assignment_name}: {problem}.")


async def sync_courses(
    *,
    browser: BrowserSession,
    db: Database,
    settings: Settings,
    jail: PathJail,
    emit: Callable[[str, str, dict[str, Any] | None], None],
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Read every course's assignments and store status + due date.

    Returns a JSON-serialisable summary. Partial failures (a course whose page
    cannot be read) are collected in ``errors`` and do not discard the courses
    that did sync.
    """

    started_at = datetime.now(timezone.utc)
    summary = _empty_summary(started_at)
    stopped = should_stop or (lambda: False)

    def finish() -> dict[str, Any]:
        summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        summary["ok"] = summary["courses"] > 0
        return summary

    if not browser.is_running:
        summary["errors"].append("The browser is not connected.")
        return finish()

    ctx = ToolContext(
        settings=settings,
        db=db,
        browser=browser,
        jail=jail,
        emit=emit,
        should_stop=stopped,
        state={},
    )

    try:
        courses = await _load_courses(ctx)
        origin = ctx.origin()
    except (ToolError, BrowserError, MoodleParseError) as exc:
        summary["errors"].append(str(exc))
        return finish()

    summary["courses_total"] = len(courses)
    if not courses:
        summary["errors"].append("No courses were found on the dashboard.")
        return finish()

    page = browser.page

    # The assignment index shows due dates as display text only, so the exact
    # instants come from Moodle's calendar (what the Timeline block uses). If
    # that service is unavailable, the sync still stores status and due text.
    deadlines: dict[str, str] = {}
    try:
        deadlines = await asyncio.wait_for(
            fetch_assign_deadlines(page, origin), timeout=_DEADLINES_TIMEOUT_S
        )
    except (MoodleParseError, asyncio.TimeoutError) as exc:
        logger.info("Moodle deadline list unavailable: %s", exc)
        summary["deadlines_limited"] = True
        summary["notes"].append(
            f"Due-date alerts are limited: {exc or 'the deadline list timed out.'}"
        )

    for step, course in enumerate(courses, start=1):
        if stopped():
            summary["errors"].append("Sync stopped.")
            break
        name = str(course.get("name") or "Course")
        course_id = str(course.get("course_id") or "").strip()
        if not course_id:
            summary["errors"].append(f"{name}: no Moodle course id, skipped.")
            continue

        emit(
            "sync",
            f"Reading assignments: {name} ({step}/{len(courses)})",
            {"step": step, "total": len(courses), "course": name},
        )

        index_url = f"{origin}/mod/assign/index.php?id={course_id}"
        try:
            payload = await asyncio.wait_for(
                fetch_assignment_index(page, index_url),
                timeout=_PER_COURSE_TIMEOUT_S,
            )
            rows = map_assignment_index(payload, index_url=index_url)
        except MoodleLoginLost as exc:
            # Abort, keep what already synced: empty rows here would be a lie.
            summary["errors"].append(str(exc))
            break
        except (MoodleParseError, asyncio.TimeoutError) as exc:
            summary["errors"].append(f"{name}: {exc}")
            continue

        db_course_id = course.get("db_id")
        if not isinstance(db_course_id, int):
            db_course_id = db.upsert_course(name, str(course.get("url") or ""), course_id)

        stored: list[tuple[dict[str, Any], int]] = []
        for row in rows:
            if not row.get("due_at") and row["url"] in deadlines:
                row["due_at"] = deadlines[row["url"]]
                summary["deadlines"] += 1
            elif row.get("due_at"):
                summary["deadlines"] += 1
            assignment_id = db.record_synced_assignment(
                db_course_id,
                row["name"],
                row["url"],
                due_text=row.get("due_text") or None,
                due_at=row.get("due_at") or None,
                status=row.get("status") or STATUS_UNKNOWN,
                status_text=row.get("status_text") or None,
            )
            stored.append((row, assignment_id))

        summary["courses"] += 1
        summary["assignments"] += len(rows)
        summary["pending"] += sum(1 for row in rows if row.get("pending"))

        # One folder per course, named exactly like the Courses list, then each
        # assignment's instructions and teacher files inside it.
        try:
            course_folder(jail, name)
            summary["folders"] += 1
        except Exception as exc:  # a jail refusal must not abort the sync
            summary["notes"].append(f"{name}: folder not created ({exc}).")
            continue

        login_lost = False
        for index, (row, assignment_id) in enumerate(stored, start=1):
            if stopped():
                break
            emit(
                "sync",
                f"Saving files: {row['name']} ({name})",
                {
                    "phase": "files",
                    "course": name,
                    "assignment": row["name"],
                    "step": step,
                    "total": len(courses),
                    "item": index,
                    "items": len(stored),
                },
            )
            try:
                await _save_assignment_files(
                    browser=browser,
                    db=db,
                    settings=settings,
                    jail=jail,
                    course_name=name,
                    course_id=course_id,
                    row=row,
                    assignment_id=assignment_id,
                    summary=summary,
                )
            except MoodleLoginLost as exc:
                summary["errors"].append(str(exc))
                login_lost = True
                break
            except (MoodleParseError, asyncio.TimeoutError) as exc:
                summary["notes"].append(
                    f"{row['name']}: page not saved ({exc or 'timed out'})."
                )
            except Exception as exc:  # e.g. a jail refusal on an odd name
                logger.warning("Could not save files for %s", row["name"], exc_info=True)
                summary["notes"].append(f"{row['name']}: files not saved ({exc}).")
        if login_lost:
            break

    logger.info(
        "Moodle sync finished: %s courses, %s assignments, %s errors",
        summary["courses"],
        summary["assignments"],
        len(summary["errors"]),
    )
    return finish()
