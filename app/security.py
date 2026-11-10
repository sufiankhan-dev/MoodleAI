"""Security primitives: URL validation, path jails, safe filenames.

These functions are the only sanctioned way for tools to touch the network or
the filesystem. Model-generated arguments are always treated as untrusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_UNSAFE_NAME_CHARS = re.compile(r"[^\w\s.\-()\[\]]", re.UNICODE)
_URL_QUERY_SECRETS = {"sesskey", "token", "password", "passwd", "pwd", "key", "auth"}


class SecurityError(Exception):
    """Base class for rejected operations."""


class UrlSecurityError(SecurityError):
    """Raised when a URL fails validation."""


class PathSecurityError(SecurityError):
    """Raised when a path would escape an allowed root."""


def validate_http_url(raw: str, *, allow_loopback: bool = False) -> str:
    """Validate and normalize an http(s) URL.

    Rejects credentials-in-URL, control characters, non-web schemes, and
    (optionally) loopback targets that a model should never browse.
    """
    if not isinstance(raw, str):
        raise UrlSecurityError("URL must be a string")
    candidate = raw.strip()
    if not candidate:
        raise UrlSecurityError("URL is empty")
    if len(candidate) > 2048:
        raise UrlSecurityError("URL is too long")
    if any(ord(ch) < 32 for ch in candidate):
        raise UrlSecurityError("URL contains control characters")
    if "\\" in candidate:
        raise UrlSecurityError("URL contains a backslash")

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UrlSecurityError(f"Only http(s) URLs are allowed, got {scheme or 'no scheme'!r}")
    if parsed.username or parsed.password:
        raise UrlSecurityError("URLs with embedded credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise UrlSecurityError("URL has no host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlSecurityError("URL has an invalid port") from exc

    if not allow_loopback and host in {"localhost", "127.0.0.1", "::1"}:
        raise UrlSecurityError("Loopback addresses are not valid Moodle navigation targets")

    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    else:
        netloc = host
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


@dataclass(frozen=True)
class UrlPolicy:
    """Navigation allowlist based on the configured Moodle host."""

    base_hosts: tuple[str, ...]

    @classmethod
    def from_settings(cls, moodle_url: str | None) -> "UrlPolicy":
        if not moodle_url:
            return cls(())
        host = urlparse(moodle_url).hostname
        return cls((host.lower().rstrip("."),) if host else ())

    def add_host(self, url: str) -> "UrlPolicy":
        host = urlparse(url).hostname
        if not host:
            return self
        host = host.lower().rstrip(".")
        if host in self.base_hosts:
            return self
        return UrlPolicy((*self.base_hosts, host))

    def permits(self, url: str, *, allow_subdomains: bool = True) -> bool:
        host = urlparse(url).hostname
        if not host:
            return False
        host = host.lower().rstrip(".")
        for base in self.base_hosts:
            if host == base:
                return True
            if allow_subdomains and host.endswith("." + base):
                return True
        return False


def sanitize_filename(name: str, *, fallback: str = "file", max_length: int = 120) -> str:
    """Turn an untrusted attachment name into a safe basename."""
    base = str(name).replace("\\", "/").split("/")[-1]
    base = _INVALID_FILENAME_CHARS.sub("_", base)
    base = re.sub(r"\s+", " ", base).strip().strip(".")
    if not base:
        return fallback
    stem = Path(base).stem
    suffix = Path(base).suffix
    if suffix and len(suffix) > 12:
        suffix = ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    room = max(1, max_length - len(suffix))
    stem = stem[:room].rstrip(" .")
    return (stem + suffix) or fallback


def sanitize_component(name: str, *, fallback: str = "item", max_length: int = 80) -> str:
    """Sanitize a course/assignment folder name while keeping it readable."""
    cleaned = _UNSAFE_NAME_CHARS.sub("_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or fallback


class PathJail:
    """Confines every file operation to explicitly registered roots."""

    def __init__(self, roots: dict[str, Path]) -> None:
        if not roots:
            raise ValueError("PathJail needs at least one root")
        self._roots = {key: Path(value).resolve() for key, value in roots.items()}

    @property
    def roots(self) -> dict[str, Path]:
        return dict(self._roots)

    def root(self, root_name: str) -> Path:
        try:
            return self._roots[root_name]
        except KeyError as exc:
            raise PathSecurityError(f"Unknown storage root: {root_name!r}") from exc

    def resolve(
        self,
        root_name: str,
        relative: str,
        *,
        must_exist: bool = False,
        create_parents: bool = False,
    ) -> Path:
        """Resolve a relative path under ``root_name`` or raise."""
        if not isinstance(relative, str) or not relative.strip():
            raise PathSecurityError("Path is empty")
        if "\x00" in relative:
            raise PathSecurityError("Path contains a null byte")
        if relative.startswith("~"):
            raise PathSecurityError("Home-relative paths are not allowed")

        candidate_path = Path(relative)
        if candidate_path.is_absolute() or candidate_path.drive or candidate_path.anchor:
            raise PathSecurityError("Absolute paths are not allowed")
        for part in candidate_path.parts:
            if part in {"..", "."}:
                raise PathSecurityError("Parent-directory traversal is not allowed")
            if part.rstrip(" .").upper() in _WINDOWS_RESERVED:
                raise PathSecurityError(f"Reserved Windows name: {part!r}")

        base = self.root(root_name)
        resolved = (base / candidate_path).resolve(strict=False)
        if resolved != base and not resolved.is_relative_to(base):
            raise PathSecurityError("Path escapes its allowed workspace root")

        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        if must_exist and not resolved.exists():
            raise PathSecurityError(f"Path does not exist: {relative}")
        return resolved

    def relative(self, root_name: str, path: Path) -> str:
        """Return a portable path relative to a root, for display/DB storage."""
        base = self.root(root_name)
        resolved = Path(path).resolve()
        if resolved != base and not resolved.is_relative_to(base):
            raise PathSecurityError("Path is outside its allowed root")
        return resolved.relative_to(base).as_posix()


def redact_url(url: str) -> str:
    """Render a URL safe for logs/UI: no query string, no fragment."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid-url>"
        suffix = "?…" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path or '/'}{suffix}"
    except ValueError:
        return "<invalid-url>"


def truncate_text(text: str, limit: int, marker: str = "\n…[truncated]") -> str:
    """Cap untrusted text before it reaches the model or the UI."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))] + marker
