"""Tool contracts shared by every capability exposed to the model.

Only tools registered here are visible to qwen3:8b. Handlers receive validated
Pydantic arguments and must return JSON-serializable dicts. No handler may
execute shell commands or touch paths outside the configured jail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.security import PathJail
from browser.session import BrowserError, BrowserSession
from database.db import Database

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """A recoverable tool failure; the message is fed back to the model."""

    def __init__(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data or {}


class ToolCancelled(RuntimeError):
    """Raised when the user pressed Stop during a tool call."""


class StrictArgs(BaseModel):
    """Base for tool arguments; unknown keys are ignored for model robustness."""

    model_config = ConfigDict(extra="ignore")


@dataclass
class ToolContext:
    """Everything a tool is allowed to use, injected by the agent runner."""

    settings: Settings
    db: Database
    browser: BrowserSession
    jail: PathJail
    emit: Callable[[str, str, dict[str, Any] | None], None] = field(
        default=lambda *_args, **_kwargs: None
    )
    approve: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None
    should_stop: Callable[[], bool] = field(default=lambda: False)
    state: dict[str, Any] = field(default_factory=dict)

    def check_cancel(self) -> None:
        if self.should_stop():
            raise ToolCancelled("Stopped by the user")

    def log(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        try:
            self.emit(kind, message, data)
        except Exception:
            logger.debug("Tool event emission failed", exc_info=True)

    async def require_approval(self, action: str, details: dict[str, Any]) -> bool:
        """Ask the user before a consequential or destructive action."""
        if self.approve is None:
            return False
        self.check_cancel()
        self.log("approval", f"Waiting for approval: {action}", details)
        approved = await self.approve(action, details)
        self.check_cancel()
        return bool(approved)

    async def require_login(self) -> None:
        try:
            await self.browser.require_login()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    def origin(self) -> str:
        """Current scheme://host, used to build Moodle-relative URLs."""
        from urllib.parse import urlparse

        page = self.browser.page
        parsed = urlparse(page.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ToolError("The browser is not on a Moodle page yet.")
        return f"{parsed.scheme}://{parsed.netloc}"

    def current_course(self) -> dict[str, Any] | None:
        course = self.state.get("current_course")
        return course if isinstance(course, dict) else None

    def current_assignment(self) -> dict[str, Any] | None:
        assignment = self.state.get("current_assignment")
        return assignment if isinstance(assignment, dict) else None

    def known_courses(self) -> list[str]:
        """Synced course names, so workspace folders match the Courses list."""
        try:
            return [str(row["name"]) for row in self.db.list_courses() if row.get("name")]
        except Exception:
            logger.debug("Could not read known courses", exc_info=True)
            return []


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool and its safety classification."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
    read_only: bool = True
    requires_approval: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool name: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda spec: spec.name)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.all()]

    def names(self) -> list[str]:
        return [spec.name for spec in self.all()]
