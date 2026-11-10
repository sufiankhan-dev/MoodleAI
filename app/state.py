"""Application state: browser, model, tools, agent runs, approvals, and events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.llm import OllamaService
from agent.loop import AgentRun, MoodleAgent
from app.config import Settings, load_settings
from app.events import EventBus
from app.logging_setup import configure_logging
from app.security import PathJail, redact_url, validate_http_url
from browser.session import BrowserError, BrowserSession
from database.db import Database
from tools import build_registry

logger = logging.getLogger(__name__)

_SECRET_KEYS = {
    "password",
    "passwd",
    "pwd",
    "sesskey",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "secret",
}


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Remove session/credential material before logging or broadcasting."""
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            if any(secret in str(key).lower() for secret in _SECRET_KEYS):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _sanitize(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:30]]
    if isinstance(value, str):
        text = value[:1200]
        if text.startswith(("http://", "https://")):
            return redact_url(text)
        return text
    return value


class BusyError(RuntimeError):
    """Raised when a second agent turn is started while one is running."""


class AppState:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: OllamaService | None = None,
        browser: BrowserSession | None = None,
    ) -> None:
        configure_logging(settings.log_level, settings.logs_dir)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.bus = EventBus()
        self.jail = PathJail(
            {"workspace": settings.workspace_dir, "generated": settings.generated_dir}
        )
        self.llm = llm or OllamaService(settings)
        self.browser = browser or BrowserSession(settings, on_event=self._browser_event)
        self.registry = build_registry()
        self.agent = MoodleAgent(
            settings=settings,
            db=self.db,
            browser=self.browser,
            registry=self.registry,
            llm=self.llm,
        )
        self.active_run: AgentRun | None = None
        self.active_run_id: str | None = None
        self.approvals: dict[str, asyncio.Future[bool]] = {}
        self.approval_meta: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[Any] | None = None
        self._force_stop_task: asyncio.Task[Any] | None = None
        self._agent_status = "idle"
        self._moodle_url = settings.moodle_url or self._load_saved_url()
        self._started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- events ----------

    def _browser_event(self, kind: str, message: str, data: dict[str, Any] | None) -> None:
        self.emit(kind, message, data)

    def emit(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        safe = _sanitize(data) if data is not None else None
        self.bus.publish(kind, message, safe, run_id=self.active_run_id)
        if kind == "assistant_delta":
            return
        try:
            level = "error" if kind == "error" else "info"
            self.db.log_activity(level, kind, (message or "")[:500], safe)
        except Exception:
            logger.debug("Activity log write failed", exc_info=True)

    def _set_status(self, status: str) -> None:
        self._agent_status = status
        self.emit(
            "status",
            status.replace("_", " ").capitalize(),
            {"status": status, "running": status not in {"idle"}},
        )

    def agent_state(self) -> dict[str, Any]:
        return {
            "status": self._agent_status,
            "running": self.active_run is not None,
            "run_id": self.active_run_id,
            "conversation_id": self.active_run.conversation_id if self.active_run else None,
        }

    # ---------- chat ----------

    async def start_chat(self, message: str, conversation_id: int | None = None) -> tuple[int, str]:
        if self.active_run is not None and self._task is not None and not self._task.done():
            raise BusyError("The agent is already working. Stop it or wait for it to finish.")
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("Message is empty")
        if len(cleaned) > 8000:
            raise ValueError("Message is too long (maximum 8000 characters).")

        try:
            cid = self.db.ensure_conversation(conversation_id, cleaned)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        self.db.add_message(cid, "user", cleaned)
        run = AgentRun(conversation_id=cid, user_message=cleaned)
        self.active_run = run
        self.active_run_id = uuid.uuid4().hex[:12]
        self._set_status("running")
        self.emit("user_message", cleaned, {"conversation_id": cid})
        self._task = asyncio.create_task(self._run_agent(run))
        return cid, self.active_run_id

    async def _run_agent(self, run: AgentRun) -> None:
        try:
            await self.agent.run(
                run=run,
                emit=self.emit,
                approve=self.request_approval,
            )
        except asyncio.CancelledError:
            self.emit("stopped", "Agent stopped", {"steps": run.outcome.steps})
            raise
        except Exception as exc:
            logger.exception("Agent run failed")
            self.emit("error", f"Agent run failed: {exc}", None)
        finally:
            self.active_run = None
            self.active_run_id = None
            self._task = None
            self._set_status("idle")

    async def stop_agent(self) -> bool:
        if self.active_run is None:
            return False
        self.active_run.request_stop()
        self._set_status("stopping")
        self.emit("status", "Stopping after the current step…", {"status": "stopping", "running": True})
        self.deny_all_approvals()
        if self._task is not None and not self._task.done():
            if self._force_stop_task is None or self._force_stop_task.done():
                self._force_stop_task = asyncio.create_task(self._force_stop(self._task))
        return True

    async def _force_stop(self, task: asyncio.Task[Any]) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return
        if not task.done():
            task.cancel()
            self.emit("stopped", "Agent stopped (forced)", None)

    # ---------- approvals ----------

    async def request_approval(self, action: str, details: dict[str, Any]) -> bool:
        if self.active_run is None:
            return False
        loop = asyncio.get_running_loop()
        approval_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[bool] = loop.create_future()
        self.approvals[approval_id] = future
        meta = {
            "approval_id": approval_id,
            "action": action,
            "details": _sanitize(details),
            "timeout_s": self.settings.approval_timeout_s,
        }
        self.approval_meta[approval_id] = meta
        self.db.create_approval(approval_id, self.active_run_id, action, _sanitize(details))
        self._set_status("waiting_approval")
        self.emit(
            "approval_required",
            f"Approval needed: {action}",
            meta,
        )

        approved = False
        timed_out = False
        try:
            approved = await asyncio.wait_for(future, timeout=self.settings.approval_timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            approved = False
            self.emit("approval_timeout", "Approval timed out; action denied", meta)
        finally:
            self.approvals.pop(approval_id, None)
            self.approval_meta.pop(approval_id, None)
            self.db.resolve_approval(
                approval_id,
                "approved" if approved else ("timeout" if timed_out else "denied"),
            )
            if self.active_run is not None:
                self._set_status("running")
        return approved

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        future = self.approvals.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(bool(approved))
        meta = self.approval_meta.get(approval_id, {"approval_id": approval_id})
        self.emit(
            "approval_resolved",
            "Approved" if approved else "Denied",
            {**meta, "approved": bool(approved)},
        )
        return True

    def deny_all_approvals(self) -> None:
        for approval_id, future in list(self.approvals.items()):
            if not future.done():
                future.set_result(False)
                self.db.resolve_approval(approval_id, "denied")
                self.emit(
                    "approval_resolved",
                    "Denied (agent stopped)",
                    {"approval_id": approval_id, "approved": False},
                )

    def pending_approvals(self) -> list[dict[str, Any]]:
        for approval_id, future in list(self.approvals.items()):
            if future.done():
                self.approvals.pop(approval_id, None)
                self.approval_meta.pop(approval_id, None)
        return list(self.approval_meta.values())

    # ---------- browser ----------

    async def connect_browser(self, url: str) -> dict[str, Any]:
        try:
            validated = validate_http_url(url, allow_loopback=True)
        except Exception as exc:
            raise BrowserError(str(exc)) from exc

        if self.browser.is_running:
            self.browser.add_allowed_host(validated)
            await self.browser.navigate(validated, allow_external=True)
        else:
            await self.browser.start(validated)

        self._moodle_url = validated
        self._save_state()
        self.emit("browser", "Moodle opened in visible Chrome", {"url": redact_url(validated)})
        return await self.browser_status(refresh=False)

    async def disconnect_browser(self) -> None:
        await self.browser.close()

    async def browser_status(self, *, refresh: bool = True) -> dict[str, Any]:
        if refresh and self.browser.is_running:
            try:
                await self.browser.refresh_status()
            except Exception:
                logger.debug("Browser status refresh failed", exc_info=True)
        status = self.browser.status.to_dict()
        status["moodle_url"] = self._moodle_url or ""
        if self.browser.is_running:
            try:
                status["page"] = await self.browser.current_page()
            except Exception:
                status["page"] = None
        else:
            status["page"] = None
        return status

    # ---------- state persistence ----------

    def _state_file(self) -> Path:
        return self.settings.config_dir / "app_state.json"

    def _load_saved_url(self) -> str | None:
        path = self._state_file()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            url = data.get("moodle_url")
            if isinstance(url, str) and url:
                return validate_http_url(url, allow_loopback=True)
        except Exception:
            logger.debug("Could not read saved app state", exc_info=True)
        return None

    def _save_state(self) -> None:
        path = self._state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(
            json.dumps({"moodle_url": self._moodle_url or ""}, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)

    # ---------- data reset ----------

    @staticmethod
    def _clear_tree(root: Path) -> int:
        """Delete everything under ``root`` but keep ``.gitkeep`` placeholders.

        Returns the number of files and directories removed.
        """

        if not root.exists():
            return 0

        removed = 0
        # Deepest first: files go before the directories that hold them.
        entries = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for entry in entries:
            if entry.name == ".gitkeep":
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    if any(entry.iterdir()):
                        continue
                    entry.rmdir()
                else:
                    entry.unlink()
                removed += 1
            except OSError:
                logger.debug("Reset could not remove %s", entry, exc_info=True)
        return removed

    def reset_local_data(self) -> dict[str, Any]:
        """Clear every locally stored record and generated file.

        Courses, assignments, file records, conversations, messages, activity and
        approvals are dropped from SQLite, and ``workspace/`` plus ``generated/``
        are emptied. The saved Moodle URL and the Chrome login profile are kept so
        the user does not have to log in again.
        """

        if self.active_run is not None and self._task is not None and not self._task.done():
            raise BusyError("The agent is still working. Stop it before resetting saved data.")

        self.deny_all_approvals()
        counts = self.db.reset_data()
        summary: dict[str, Any] = {
            "courses": counts.get("courses", 0),
            "assignments": counts.get("assignments", 0),
            "files": counts.get("files", 0),
            "conversations": counts.get("conversations", 0),
            "messages": counts.get("messages", 0),
            "activity": counts.get("activity_log", 0),
            "approvals": counts.get("approvals", 0),
            "workspace_files": self._clear_tree(self.settings.workspace_dir),
            "generated_files": self._clear_tree(self.settings.generated_dir),
            "login_kept": True,
        }
        # Logged after the wipe so the activity panel shows the reset itself.
        self.emit("reset", "Saved data cleared", summary)
        return {"ok": True, **summary}

    # ---------- read models ----------

    def _tree(
        self,
        root_name: str,
        depth: int = 0,
        counter: list[int] | None = None,
        base: Path | None = None,
    ) -> list[dict[str, Any]]:
        if base is None:
            base = self.jail.root(root_name)
            base.mkdir(parents=True, exist_ok=True)
        counter = counter if counter is not None else [0]
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return entries

        for child in children:
            if counter[0] >= 600:
                entries.append({"type": "notice", "name": "… more files not shown"})
                break
            if child.name == ".gitkeep" or child.is_symlink():
                continue
            counter[0] += 1
            relative = self.jail.relative(root_name, child)
            if child.is_dir():
                node: dict[str, Any] = {
                    "type": "dir",
                    "name": child.name,
                    "path": relative,
                    "children": [],
                }
                if depth < 4:
                    node["children"] = self._tree(root_name, depth + 1, counter, child)
                entries.append(node)
            elif child.is_file():
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append(
                    {
                        "type": "file",
                        "name": child.name,
                        "path": relative,
                        "root": root_name,
                        "size": size,
                        "download_url": f"/api/files/download?root={root_name}&path={relative}",
                    }
                )
        return entries

    def workspace_tree(self) -> dict[str, Any]:
        return {
            "workspace": self._tree("workspace"),
            "generated": self._tree("generated"),
        }

    def file_for_download(self, root: str, relative: str) -> Path:
        if root not in {"workspace", "generated"}:
            raise ValueError("Unknown file root")
        path = self.jail.resolve(root, relative, must_exist=True)
        if not path.is_file():
            raise ValueError("Not a file")
        return path

    def assignment_rows(self) -> list[dict[str, Any]]:
        rows = self.db.list_assignments()
        for row in rows:
            status_text = (row.get("status_text") or "").lower()
            submitted = (
                any(token in status_text for token in ("submitted", "graded"))
                and "not submitted" not in status_text
                and "no attempt" not in status_text
            )
            row["pending"] = not submitted
        return rows

    def conversation_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        return self.db.get_messages(conversation_id, limit=200)

    def tool_list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "read_only": spec.read_only,
                "requires_approval": spec.requires_approval,
            }
            for spec in self.registry.all()
        ]

    async def snapshot(self) -> dict[str, Any]:
        return {
            "ollama": await self.llm.health(),
            "browser": await self.browser_status(),
            "moodle_url": self._moodle_url or "",
            "agent": self.agent_state(),
            "courses": self.db.list_courses(),
            "assignments": self.assignment_rows(),
            "tools": self.tool_list(),
            "activity": self.db.list_activity(150),
            "conversations": self.db.list_conversations(20),
            "files": self.workspace_tree(),
            "approvals": self.pending_approvals(),
            "started_at": self._started_at,
        }

    async def light_snapshot(self) -> dict[str, Any]:
        return {
            "browser": await self.browser_status(refresh=False),
            "agent": self.agent_state(),
            "approvals": self.pending_approvals(),
            "moodle_url": self._moodle_url or "",
        }

    # ---------- shutdown ----------

    async def shutdown(self) -> None:
        try:
            if self.active_run is not None:
                self.active_run.request_stop()
            self.deny_all_approvals()
            task = self._task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if self._force_stop_task is not None and not self._force_stop_task.done():
                self._force_stop_task.cancel()
                await asyncio.gather(self._force_stop_task, return_exceptions=True)
        finally:
            try:
                await self.browser.close()
            except Exception:
                logger.debug("Browser close during shutdown failed", exc_info=True)
            try:
                await self.llm.aclose()
            except Exception:
                logger.debug("LLM close during shutdown failed", exc_info=True)
            self.db.close()


def default_state() -> AppState:
    return AppState(load_settings())
