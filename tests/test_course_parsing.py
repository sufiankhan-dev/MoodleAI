"""Fast unit tests for course extraction across Moodle themes."""

from __future__ import annotations

from browser.moodle import (
    course_name_from_heading,
    is_placeholder_course_name,
    looks_like_course_code,
    parse_course_display_name,
    parse_courses,
)


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


def test_full_name_beats_the_course_code_for_the_same_course() -> None:
    # /my/courses.php lists "Course name" and "Short name" as two links to the
    # same course; the longer label used to win, which surfaced the code.
    snapshot = _snapshot(
        links=[
            ("Database Systems", "https://lms.example.edu/course/view.php?id=42", "link"),
            ("BS-1402-BS-261-BS-SE-5A", "https://lms.example.edu/course/view.php?id=42", "link"),
        ]
    )

    courses = parse_courses(snapshot)
    assert len(courses) == 1
    assert courses[0]["name"] == "Database Systems"


def test_code_is_kept_when_it_is_the_only_label() -> None:
    snapshot = _snapshot(
        links=[("CS-3301-CS-261-BS-SE-5B", "https://lms.example.edu/course/view.php?id=7", "link")]
    )

    assert [c["name"] for c in parse_courses(snapshot)] == ["CS-3301-CS-261-BS-SE-5B"]


def test_card_name_beats_a_code_link() -> None:
    snapshot = _snapshot(
        links=[("SE-3802L-CS-261-BS-SE-5B", "https://lms.example.edu/course/view.php?id=12", "link")],
        cards=[{"course_id": "12", "name": "Software Engineering Lab", "href": ""}],
    )

    courses = parse_courses(snapshot)
    assert courses[0]["name"] == "Software Engineering Lab"


def test_course_display_name_reads_the_page_heading() -> None:
    html = """
    <html><head><title>Course: Database Systems | LMS</title></head>
    <body><div class="page-header-headings"><h1 class="h2">Database Systems</h1></div></body></html>
    """

    assert parse_course_display_name(html) == "Database Systems"


def test_course_display_name_falls_back_to_the_title() -> None:
    html = "<html><head><title>Course: Operating Systems | DSU LMS</title></head><body></body></html>"

    assert parse_course_display_name(html, site_title="DSU LMS") == "Operating Systems"


def test_course_display_name_ignores_login_pages() -> None:
    html = "<html><head><title>Log in to the site</title></head><body><h1>Log in to the site</h1></body></html>"

    assert parse_course_display_name(html) == ""
    assert parse_course_display_name("") == ""


def test_course_name_from_heading_prefers_the_page_heading() -> None:
    assert (
        course_name_from_heading(
            "Computer Organization & Assembly Language(BS-CYS-5A)",
            "Course: Computer Organization & Assembly Language(BS-CYS-5A) | DSU LMS",
        )
        == "Computer Organization & Assembly Language(BS-CYS-5A)"
    )
    assert course_name_from_heading("", "Course: Operating Systems | DSU LMS") == "Operating Systems"
    assert course_name_from_heading("Log in to the site", "Log in to the site") == ""


def test_course_code_detection() -> None:
    assert looks_like_course_code("BS-1402-BS-261-BS-SE-5A")
    assert looks_like_course_code("CS 101")
    assert not looks_like_course_code("Database Systems")
    assert not looks_like_course_code("DATABASE SYSTEMS")
    assert not looks_like_course_code("Lab 1 - Normalization")
    assert not looks_like_course_code("")


def test_placeholder_names_are_detected() -> None:
    assert is_placeholder_course_name("")
    assert is_placeholder_course_name("Course")
    assert is_placeholder_course_name("Course 10581", "10581")
    assert is_placeholder_course_name("Open course")
    assert not is_placeholder_course_name("Database Systems", "10581")


def test_placeholder_card_does_not_beat_a_readable_link() -> None:
    # A card whose image has no alt text yields no name; the anchor label wins.
    snapshot = _snapshot(
        links=[("Database Systems", "https://lms.example.edu/course/view.php?id=42", "link")],
        cards=[{"course_id": "42", "name": "Course 42", "href": ""}],
    )

    courses = parse_courses(snapshot)
    assert [c["name"] for c in courses] == ["Database Systems"]
    assert courses[0]["placeholder_name"] is False


def test_course_without_a_label_is_reported_as_a_placeholder() -> None:
    snapshot = _snapshot(
        cards=[{"course_id": "10581", "name": "", "href": ""}],
    )

    courses = parse_courses(snapshot)
    assert courses[0]["name"] == "Course 10581"
    assert courses[0]["placeholder_name"] is True


def test_ignored_course_is_dropped_from_links_and_cards() -> None:
    snapshot = _snapshot(
        url="https://lms.dsu.edu.pk/my/",
        links=[
            ("Programming Fundamentals Lab(BS-SE-1B)",
             "https://lms.dsu.edu.pk/course/view.php?id=6870", "link"),
            ("Database Systems", "https://lms.dsu.edu.pk/course/view.php?id=6871", "link"),
        ],
        cards=[{"course_id": "6870", "name": "Programming Fundamentals Lab", "href": ""}],
    )

    assert [c["course_id"] for c in parse_courses(snapshot)] == ["6871"]


def test_same_course_id_on_another_moodle_is_not_ignored() -> None:
    snapshot = _snapshot(
        links=[("Algebra", "https://lms.example.edu/course/view.php?id=6870", "link")]
    )

    assert [c["course_id"] for c in parse_courses(snapshot)] == ["6870"]
