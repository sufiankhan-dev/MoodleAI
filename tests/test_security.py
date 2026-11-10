"""Phase 2 tests: URL policy, filename safety, and the path jail."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security import (
    PathJail,
    PathSecurityError,
    UrlPolicy,
    UrlSecurityError,
    redact_url,
    sanitize_component,
    sanitize_filename,
    truncate_text,
    validate_http_url,
)


class TestUrlValidation:
    def test_accepts_normal_moodle_url(self) -> None:
        url = validate_http_url("https://moodle.example.edu/course/view.php?id=2")
        assert url == "https://moodle.example.edu/course/view.php?id=2"

    def test_strips_fragment(self) -> None:
        url = validate_http_url("https://moodle.example.edu/x#section")
        assert "#" not in url

    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "file:///C:/Windows/win.ini",
            "data:text/html,<h1>x</h1>",
            "ftp://example.com/file",
            "https://user:pass@example.com/",
            "//example.com/path",
            "https:///nohost",
            "https://example.com/\x00evil",
            "https://example.com\\@evil.com/",
            "",
        ],
    )
    def test_rejects_unsafe_urls(self, bad: str) -> None:
        with pytest.raises(UrlSecurityError):
            validate_http_url(bad)

    def test_rejects_loopback_by_default(self) -> None:
        with pytest.raises(UrlSecurityError):
            validate_http_url("http://127.0.0.1:8080/")

    def test_allows_loopback_when_explicit(self) -> None:
        assert validate_http_url("http://127.0.0.1:8080/", allow_loopback=True)

    def test_rejects_overlong_url(self) -> None:
        with pytest.raises(UrlSecurityError):
            validate_http_url("https://example.com/" + "a" * 3000)


class TestUrlPolicy:
    def test_permits_base_and_subdomains(self) -> None:
        policy = UrlPolicy.from_settings("https://moodle.example.edu")
        assert policy.permits("https://moodle.example.edu/course/view.php?id=1")
        assert policy.permits("https://lms.moodle.example.edu/")
        assert not policy.permits("https://evil-example.edu/")
        assert not policy.permits("https://example.edu.evil.com/")

    def test_empty_policy_permits_nothing(self) -> None:
        assert not UrlPolicy.from_settings(None).permits("https://example.com/")


class TestFilenames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("C:\\Windows\\evil.exe", "evil.exe"),
            ('bad<>:"|?*name.pdf', "bad_______name.pdf"),
            ("CON.txt", "_CON.txt"),
            ("  spaced  name .pdf  ", "spaced name.pdf"),
        ],
    )
    def test_sanitize_filename(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_sanitize_filename_limits_length(self) -> None:
        assert len(sanitize_filename("a" * 500 + ".pdf")) <= 120

    def test_sanitize_component(self) -> None:
        assert sanitize_component("Database Systems: Section 2") == "Database Systems_ Section 2"
        assert sanitize_component("..") == "item"


class TestPathJail:
    def test_resolves_inside_root(self, tmp_path: Path) -> None:
        jail = PathJail({"workspace": tmp_path / "ws"})
        target = jail.resolve("workspace", "Course/Assignment/file.txt", create_parents=True)
        assert target.is_relative_to(tmp_path / "ws")
        assert target.parent.exists()

    @pytest.mark.parametrize(
        "bad",
        ["../escape.txt", "a/../../escape.txt", "C:\\Windows\\x.txt", "\\\\server\\share\\x", ""],
    )
    def test_rejects_escapes_and_absolute_paths(self, tmp_path: Path, bad: str) -> None:
        jail = PathJail({"workspace": tmp_path / "ws"})
        with pytest.raises(PathSecurityError):
            jail.resolve("workspace", bad)

    def test_rejects_unknown_root(self, tmp_path: Path) -> None:
        jail = PathJail({"workspace": tmp_path})
        with pytest.raises(PathSecurityError):
            jail.resolve("generated", "x.txt")

    def test_must_exist(self, tmp_path: Path) -> None:
        jail = PathJail({"workspace": tmp_path})
        with pytest.raises(PathSecurityError):
            jail.resolve("workspace", "missing.txt", must_exist=True)

    def test_relative_round_trip(self, tmp_path: Path) -> None:
        jail = PathJail({"workspace": tmp_path})
        resolved = jail.resolve("workspace", "a/b.txt", create_parents=True)
        assert jail.relative("workspace", resolved) == "a/b.txt"


def test_redact_url_drops_query() -> None:
    redacted = redact_url("https://moodle.example.edu/course/view.php?id=2&sesskey=SECRET")
    assert "SECRET" not in redacted
    assert "moodle.example.edu" in redacted
    assert redacted.endswith("?…")


def test_truncate_text() -> None:
    assert truncate_text("abcdef", 10) == "abcdef"
    result = truncate_text("a" * 100, 20)
    assert len(result) == 20
    assert result.endswith("[truncated]")
