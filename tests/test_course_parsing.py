"""Fast unit tests for course extraction across Moodle themes."""

from __future__ import annotations

from browser.moodle import parse_courses


def _snapshot(*, links=(), cards=(), url="https://lms.example.edu/my/"):
    return {
        "full_url": url,
        "url": url,
        "title": "Dashboard",
        "page_type": "dashboard",
        "links": [
            {"text": text, "href": href, "kind": kind}
            for text, href, kind in links
        ],
        "course_cards": list(cards),
        "activities": [],
    }


def test_course_links_are_found_anywhere_on_the_page() -> None:
    snapshot = _snapshot(
        links=[
            ("Home", "https://lms.example.edu/", "link"),
            ("DATABASE SYSTEMS", "https://lms.example.edu/course/view.php?id=2", "link"),
        ]
    )

    courses = parse_courses(snapshot)
    assert [c["name"] for c in courses] == ["DATABASE SYSTEMS"]
    assert courses[0]["course_id"] == "2"
    assert courses[0]["url"] == "https://lms.example.edu/course/view.php?id=2"


def test_course_cards_without_anchors_build_urls_from_the_page_origin() -> None:
    snapshot = _snapshot(
        cards=[
            {"course_id": "17", "name": "Discrete Structures", "href": ""},
            {"course_id": "21", "name": "Operating Systems", "href": ""},
        ]
    )

    courses = parse_courses(snapshot)
    assert [c["name"] for c in courses] == ["Discrete Structures", "Operating Systems"]
    assert courses[0]["url"] == "https://lms.example.edu/course/view.php?id=17"
    assert courses[1]["course_id"] == "21"


def test_card_and_link_for_the_same_course_are_merged_with_the_best_name() -> None:
    snapshot = _snapshot(
        links=[("Open", "https://lms.example.edu/course/view.php?id=9", "link")],
        cards=[
            {"course_id": "9", "name": "Software Quality Engineering", "href": ""},
        ],
    )

    courses = parse_courses(snapshot)
    assert len(courses) == 1
    assert courses[0]["name"] == "Software Quality Engineering"
    assert courses[0]["url"] == "https://lms.example.edu/course/view.php?id=9"


def test_non_course_links_and_blank_cards_are_ignored() -> None:
    snapshot = _snapshot(
        links=[
            ("Forum", "https://lms.example.edu/mod/forum/view.php?id=4", "link"),
            ("Other query", "https://lms.example.edu/some/page.php?id=5", "link"),
            ("No id", "https://lms.example.edu/course/view.php", "link"),
        ],
        cards=[
            {"course_id": "", "name": "Ghost card", "href": ""},
            {"course_id": "", "name": "", "href": ""},
        ],
    )

    assert parse_courses(snapshot) == []


def test_course_path_match_is_case_insensitive() -> None:
    snapshot = _snapshot(
        links=[("Physics", "https://lms.example.edu/Course/View.php?id=33", "link")]
    )

    courses = parse_courses(snapshot)
    assert len(courses) == 1
    assert courses[0]["course_id"] == "33"
    # Normalised to the query key used for de-duplication.
    assert courses[0]["url"] == "https://lms.example.edu/Course/View.php?id=33"
