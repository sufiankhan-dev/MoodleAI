"""Automatic Moodle course + assignment sync for the dashboard.

The app runs this on its own initiative (after the user logs in, or from the
Sync button); the model never triggers it, so the tool registry stays the
model's only surface. Everything here is read-only against Moodle: it reads the
course list and each course's assignment index, and nothing else. It never
touches ``workspace/``, so no approval gate is involved — only local database
rows are written.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import Settings
from app.security import PathJail
from browser.moodle import (
    STATUS_LABELS,
    STATUS_UNKNOWN,
    MoodleLoginLost,
    MoodleParseError,
    fetch_assign_deadlines,
    fetch_assignment_index,
    is_pending,
    map_assignment_index,
    normalize_submission_status,
)
from browser.session import BrowserError, BrowserSession
from database.db import Database
from tools.base import ToolContext, ToolError

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
        "errors": [],
        "notes": [],
    }


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

        for row in rows:
            if not row.get("due_at") and row["url"] in deadlines:
                row["due_at"] = deadlines[row["url"]]
                summary["deadlines"] += 1
            elif row.get("due_at"):
                summary["deadlines"] += 1
            db.record_synced_assignment(
                db_course_id,
                row["name"],
                row["url"],
                due_text=row.get("due_text") or None,
                due_at=row.get("due_at") or None,
                status=row.get("status") or STATUS_UNKNOWN,
                status_text=row.get("status_text") or None,
            )

        summary["courses"] += 1
        summary["assignments"] += len(rows)
        summary["pending"] += sum(1 for row in rows if row.get("pending"))

    logger.info(
        "Moodle sync finished: %s courses, %s assignments, %s errors",
        summary["courses"],
        summary["assignments"],
        len(summary["errors"]),
    )
    return finish()
