"""Fast tests for the sync table parser, status vocabulary, and due badges.

No browser and no model: these are the pure functions the sync and the
dashboard lean on, so the boundaries (overdue, urgent, soon) are pinned down.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.sync import (
    URGENCY_NONE,
    URGENCY_OVERDUE,
    URGENCY_SOON,
    URGENCY_UPCOMING,
    URGENCY_URGENT,
    assignment_view,
    due_urgency,
)
from browser.moodle import (
    STATUS_DRAFT,
    STATUS_GRADED,
    STATUS_NOT_REQUIRED,
    STATUS_NOT_SUBMITTED,
    STATUS_REOPENED,
    STATUS_SUBMITTED_FOR_GRADING,
    STATUS_UNKNOWN,
    is_pending,
    map_action_events,
    map_assignment_index,
    normalize_submission_status,
    parse_due_at,
)

INDEX_URL = "https://lms.example.edu/mod/assign/index.php?id=2"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", STATUS_UNKNOWN),
        ("   ", STATUS_UNKNOWN),
        ("No attempt", STATUS_NOT_SUBMITTED),
        ("No submission", STATUS_NOT_SUBMITTED),
        ("Reopened", STATUS_REOPENED),
        ("Not submitted", STATUS_NOT_SUBMITTED),
        ("Nothing submitted", STATUS_NOT_SUBMITTED),
        ("Draft (not submitted)", STATUS_DRAFT),
        ("Submitted for grading", STATUS_SUBMITTED_FOR_GRADING),
        ("Graded", STATUS_GRADED),
        ("No submission required", STATUS_NOT_REQUIRED),
        ("Submission not required", STATUS_NOT_REQUIRED),
        ("Weird theme label", STATUS_UNKNOWN),
    ],
)
def test_submission_status_vocabulary(text: str, expected: str) -> None:
    assert normalize_submission_status(text) == expected


def test_unknown_status_is_not_reported_as_pending() -> None:
    """A blank cell means Moodle said nothing, not that work is owed."""

    assert is_pending(STATUS_NOT_SUBMITTED) is True
    assert is_pending(STATUS_DRAFT) is True
    assert is_pending(STATUS_UNKNOWN) is False
    assert is_pending(STATUS_GRADED) is False


def test_due_at_accepts_epoch_and_offset_iso_but_never_guesses() -> None:
    assert parse_due_at("1763315940") == "2025-11-16T17:59:00+00:00"
    assert parse_due_at("1763315940000") == "2025-11-16T17:59:00+00:00"
    assert parse_due_at("2026-11-15T23:59:00+05:00") == "2026-11-15T18:59:00+00:00"
    assert parse_due_at("2026-11-15T18:59:00Z") == "2026-11-15T18:59:00+00:00"
    # Display text and timezone-less values are rejected on purpose: guessing
    # would put the badge hours off.
    assert parse_due_at("Sunday, 15 November 2026, 11:59 PM") is None
    assert parse_due_at("2026-11-15T18:59:00") is None
    assert parse_due_at("") is None


def test_assignment_index_uses_header_columns() -> None:
    payload = {
        "headers": ["", "Assignment name", "Due date", "Submission status", "Grade"],
        "rows": [
            {
                "name": "Normalization Exercise",
                "href": "/mod/assign/view.php?id=10",
                "cells": [
                    "",
                    "Normalization Exercise",
                    "Sunday, 15 November 2026, 11:59 PM",
                    "Submitted for grading",
                    "85.00",
                ],
                "datetime": "2026-11-15T18:59:00+00:00",
            },
            {
                "name": "ER Diagram",
                "href": "/mod/assign/view.php?id=11",
                "cells": ["", "ER Diagram", "No due date", "No attempt", "-"],
                "datetime": "",
            },
        ],
    }

    rows = map_assignment_index(payload, index_url=INDEX_URL)
    assert [row["name"] for row in rows] == ["ER Diagram", "Normalization Exercise"]

    er, normalization = rows
    assert er["url"] == "https://lms.example.edu/mod/assign/view.php?id=11"
    assert er["status"] == STATUS_NOT_SUBMITTED
    assert er["pending"] is True
    assert er["due_at"] is None
    assert er["due_text"] == "No due date"

    assert normalization["status"] == STATUS_SUBMITTED_FOR_GRADING
    assert normalization["pending"] is False
    assert normalization["due_at"] == "2026-11-15T18:59:00+00:00"
    assert "15 November 2026" in normalization["due_text"]


def test_assignment_index_falls_back_to_cell_content_without_headers() -> None:
    payload = {
        "headers": [],
        "rows": [
            {
                "name": "Quiz 2",
                "href": "view.php?id=12",
                "cells": [
                    "Quiz 2",
                    "Sunday, 15 November 2026, 11:59 PM",
                    "Not submitted",
                ],
                "datetime": "",
            }
        ],
    }

    rows = map_assignment_index(payload, index_url=INDEX_URL)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://lms.example.edu/mod/assign/view.php?id=12"
    assert "15 November 2026" in rows[0]["due_text"]
    assert rows[0]["status"] == STATUS_NOT_SUBMITTED


def test_non_assignment_rows_are_ignored() -> None:
    payload = {
        "headers": ["Assignment name", "Due date", "Submission status"],
        "rows": [
            {
                "name": "Week 4 slides",
                "href": "/mod/resource/view.php?id=4",
                "cells": ["Week 4 slides", "", ""],
                "datetime": "",
            }
        ],
    }
    assert map_assignment_index(payload, index_url=INDEX_URL) == []


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _iso(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat(timespec="seconds")


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (70, URGENCY_UPCOMING),
        (30, URGENCY_SOON),
        (2, URGENCY_URGENT),
        (-3, URGENCY_OVERDUE),
    ],
)
def test_due_urgency_boundaries(hours: float, expected: str) -> None:
    result = due_urgency(
        _iso(hours), soon_hours=48, urgent_hours=24, now=NOW
    )
    assert result["urgency"] == expected
    assert result["due_in_hours"] == pytest.approx(hours, abs=0.1)


def test_due_urgency_without_a_machine_timestamp() -> None:
    for value in (None, "", "Sunday, 15 November 2026, 11:59 PM"):
        assert due_urgency(value, soon_hours=48, urgent_hours=24, now=NOW) == {
            "urgency": URGENCY_NONE,
            "due_in_hours": None,
        }


def test_assignment_view_prefers_the_stored_status_over_stale_text() -> None:
    row = {
        "name": "Normalization Exercise",
        "status": STATUS_GRADED,
        "status_text": "Not submitted",
        "due_at": _iso(2),
        "due_text": "Thursday, 12 November 2026, 2:00 PM",
    }

    view = assignment_view(row, soon_hours=48, urgent_hours=24, now=NOW)
    assert view["status"] == STATUS_GRADED
    assert view["status_label"] == "Graded"
    assert view["pending"] is False
    assert view["urgency"] == URGENCY_URGENT


def test_assignment_view_reads_legacy_rows_without_a_status_column() -> None:
    view = assignment_view(
        {"name": "Old row", "status": None, "status_text": "Not submitted"},
        soon_hours=48,
        urgent_hours=24,
        now=NOW,
    )
    assert view["status"] == STATUS_NOT_SUBMITTED
    assert view["pending"] is True
    assert view["urgency"] == URGENCY_NONE


def test_action_events_give_exact_due_instants_for_assignments_only() -> None:
    payload = {
        "events": [
            {
                "modulename": "assign",
                "eventtype": "due",
                "url": "https://lms.example.edu/mod/assign/view.php?id=12",
                "timesort": 1763315940,
            },
            # Same assignment twice: the earliest instant wins.
            {
                "modulename": "assign",
                "eventtype": "due",
                "url": "/mod/assign/view.php?id=12",
                "timesort": 1763402340,
            },
            # Teacher-side grading deadline: not the student's due date.
            {
                "modulename": "assign",
                "eventtype": "gradingdue",
                "url": "https://lms.example.edu/mod/assign/view.php?id=13",
                "timesort": 1763315940,
            },
            {
                "modulename": "quiz",
                "eventtype": "close",
                "url": "https://lms.example.edu/mod/quiz/view.php?id=14",
                "timesort": 1763315940,
            },
            {"modulename": "assign", "eventtype": "due", "url": "", "timesort": 1763315940},
            {
                "modulename": "assign",
                "eventtype": "due",
                "url": "https://lms.example.edu/mod/assign/view.php?id=15",
                "timesort": 0,
            },
        ]
    }

    deadlines = map_action_events(payload, origin="https://lms.example.edu")
    assert deadlines == {
        "https://lms.example.edu/mod/assign/view.php?id=12": "2025-11-16T17:59:00+00:00"
    }


def test_action_event_keys_match_assignment_index_keys() -> None:
    payload = {
        "headers": ["Assignment name", "Due date", "Submission status"],
        "rows": [
            {
                "name": "Query Optimisation",
                "href": "/mod/assign/view.php?id=12",
                "cells": ["Query Optimisation", "Sunday, 16 November 2025", "No attempt"],
                "datetime": "",
            }
        ],
    }
    rows = map_assignment_index(payload, index_url=INDEX_URL)
    deadlines = map_action_events(
        {
            "events": [
                {
                    "modulename": "assign",
                    "eventtype": "due",
                    "url": "https://lms.example.edu/mod/assign/view.php?id=12&action=editsubmission",
                    "timesort": 1763315940,
                }
            ]
        },
        origin="https://lms.example.edu",
    )
    assert rows[0]["url"] in deadlines


def test_stock_moodle_no_submission_cell_is_pending() -> None:
    view = assignment_view(
        {"status": "unknown", "status_text": "No submission", "due_at": None},
        soon_hours=48,
        urgent_hours=24,
        now=NOW,
    )
    assert view["status"] == STATUS_NOT_SUBMITTED
    assert view["status_label"] == "Not submitted"
    assert view["pending"] is True
    assert is_pending(STATUS_REOPENED) is True


def test_unrecognised_status_text_is_shown_verbatim() -> None:
    view = assignment_view(
        {"status": "unknown", "status_text": "Eingereicht zur Bewertung", "due_at": None},
        soon_hours=48,
        urgent_hours=24,
        now=NOW,
    )
    assert view["status"] == STATUS_UNKNOWN
    assert view["status_label"] == "Eingereicht zur Bewertung"
    assert view["pending"] is False
