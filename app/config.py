"""Typed, validated application configuration.

All values come from environment variables (optionally loaded from ``.env``).
No secret is ever stored here: the Moodle password never enters the process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _env_str(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = _env_str(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the whole application."""

    project_root: Path
    app_host: str
    app_port: int
    log_level: str

    ollama_host: str
    ollama_model: str
    ollama_num_ctx: int
    ollama_temperature: float
    ollama_timeout_s: int
    ollama_keep_alive: str
    ollama_think: bool

    browser_channel: str
    browser_headless: bool
    browser_timeout_ms: int
    browser_profile_dir: Path
    moodle_url: str | None

    max_download_mb: int
    max_page_chars: int
    max_tool_result_chars: int
    approval_timeout_s: int
    max_agent_iterations: int

    @property
    def app_dir(self) -> Path:
        return self.project_root / "app"

    @property
    def ui_dir(self) -> Path:
        return self.app_dir / "ui"

    @property
    def workspace_dir(self) -> Path:
        return self.project_root / "workspace"

    @property
    def generated_dir(self) -> Path:
        return self.project_root / "generated"

    @property
    def database_dir(self) -> Path:
        return self.project_root / "database"

    @property
    def database_path(self) -> Path:
        return self.database_dir / "moodleai.db"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def moodle_host(self) -> str | None:
        if not self.moodle_url:
            return None
        host = urlparse(self.moodle_url).hostname
        return host.lower() if host else None

    def ensure_directories(self) -> None:
        """Create every directory the app writes to. Idempotent."""
        for path in (
            self.workspace_dir,
            self.generated_dir,
            self.database_dir,
            self.logs_dir,
            self.browser_profile_dir,
            self.config_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        for course in ("Database Systems", "Software Engineering", "Software Quality Engineering"):
            (self.workspace_dir / course).mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Build settings from the environment, validating safety-critical values."""
    host = _env_str("APP_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "APP_HOST must stay on loopback (127.0.0.1/localhost). "
            "This app must never be exposed to the network."
        )

    ollama_host = _env_str("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    parsed = urlparse(ollama_host)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"OLLAMA_HOST is not a valid http(s) URL: {ollama_host!r}")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "OLLAMA_HOST must point at the local Ollama daemon. "
            "Remote/cloud inference is deliberately unsupported."
        )

    moodle_url = _env_str("MOODLE_URL", "https://lms.dsu.edu.pk/") or None
    if moodle_url:
        parsed_moodle = urlparse(moodle_url)
        if parsed_moodle.scheme not in {"http", "https"} or not parsed_moodle.hostname:
            raise ValueError(f"MOODLE_URL must be an http(s) URL, got {moodle_url!r}")

    profile = Path(_env_str("BROWSER_PROFILE_DIR", "browser/profile"))
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile

    channel = _env_str("BROWSER_CHANNEL", "chrome")
    if channel not in {"chrome", "msedge", "chromium"}:
        raise ValueError("BROWSER_CHANNEL must be chrome, msedge, or chromium")

    settings = Settings(
        project_root=PROJECT_ROOT,
        app_host=host,
        app_port=_env_int("APP_PORT", 8765, 1024, 65535),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        ollama_host=ollama_host,
        ollama_model=_env_str("OLLAMA_MODEL", "qwen3:8b"),
        ollama_num_ctx=_env_int("OLLAMA_NUM_CTX", 16384, 2048, 65536),
        ollama_temperature=_env_float("OLLAMA_TEMPERATURE", 0.2, 0.0, 2.0),
        ollama_timeout_s=_env_int("OLLAMA_TIMEOUT_S", 600, 30, 3600),
        ollama_keep_alive=_env_str("OLLAMA_KEEP_ALIVE", "30m"),
        ollama_think=_env_bool("OLLAMA_THINK", False),
        browser_channel=channel,
        browser_headless=_env_bool("BROWSER_HEADLESS", False),
        browser_timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 30000, 5000, 180000),
        browser_profile_dir=profile,
        moodle_url=moodle_url,
        max_download_mb=_env_int("MAX_DOWNLOAD_MB", 50, 1, 500),
        max_page_chars=_env_int("MAX_PAGE_CHARS", 14000, 2000, 100000),
        max_tool_result_chars=_env_int("MAX_TOOL_RESULT_CHARS", 8000, 1000, 50000),
        approval_timeout_s=_env_int("APPROVAL_TIMEOUT_S", 300, 30, 3600),
        max_agent_iterations=_env_int("MAX_AGENT_ITERATIONS", 12, 1, 50),
    )
    settings.ensure_directories()
    return settings
