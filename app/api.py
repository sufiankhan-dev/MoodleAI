"""FastAPI backend for the local MoodleAI dashboard.

Binds to loopback only, rejects non-local Host headers, and requires a custom
header on state-changing requests to block cross-site form posts (CSRF).
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config import load_settings
from app.state import AppState, BusyError
from browser.session import BrowserError

logger = logging.getLogger(__name__)
API_VERSION = "0.1.0"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None


class ConnectRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    # Optional network/HTTP Basic-auth credentials (campus proxy dialogs). They are
    # passed straight to Chrome in memory and are never stored, logged, or sent to
    # the model.
    http_username: str = Field(default="", max_length=200)
    http_password: str = Field(default="", max_length=400)


class ApprovalDecision(BaseModel):
    approved: bool


def get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "moodleai", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Application is starting.")
    return state


def _header_host(raw: str) -> str | None:
    """Extract a lowercase hostname from a Host header value.

    Handles ``127.0.0.1:8765``, ``localhost`` and bracketed IPv6 like
    ``[::1]:8765``. Returns None when the value cannot be parsed safely.
    """

    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = urlparse(f"//{raw}")
        if "[" in raw and "]" not in raw:
            return None
        host = parsed.hostname or ""
    except ValueError:
        return None
    if not host:
        # A bare IPv6 literal has no brackets and no port; tolerate it.
        host = raw.strip("[]")
    host = host.lower()
    return host or None


def create_app(injected_state: AppState | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        state = injected_state or AppState(load_settings())
        application.state.moodleai = state
        logger.info("MoodleAI ready on http://%s:%s", state.settings.app_host, state.settings.app_port)
        try:
            yield
        finally:
            await state.shutdown()

    app = FastAPI(
        title="MoodleAI",
        version=API_VERSION,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = _header_host(request.headers.get("host") or "")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "This application only accepts local requests."}, 403)
        origin = request.headers.get("origin")
        if origin:
            try:
                origin_host = (urlparse(origin).hostname or "").lower()
            except ValueError:
                origin_host = ""
            if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse({"detail": "Cross-origin requests are blocked."}, 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("x-moodleai") != "1":
                return JSONResponse({"detail": "Missing local request header."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- static dashboard ----------

    def _ui_file(name: str, media_type: str) -> FileResponse:
        state = app.state.moodleai
        path = state.settings.ui_dir / name
        if not path.exists():
            raise HTTPException(status_code=500, detail=f"UI file missing: {name}")
        return FileResponse(path, media_type=media_type)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return _ui_file("index.html", "text/html; charset=utf-8")

    @app.get("/styles.css", include_in_schema=False)
    async def styles() -> FileResponse:
        return _ui_file("styles.css", "text/css; charset=utf-8")

    @app.get("/app.js", include_in_schema=False)
    async def script() -> FileResponse:
        return _ui_file("app.js", "application/javascript; charset=utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    # ---------- health & state ----------

    @app.get("/api/version")
    async def version() -> dict[str, str]:
        return {"version": API_VERSION, "app": "MoodleAI"}

    @app.get("/api/health")
    async def health(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {
            "ok": True,
            "version": API_VERSION,
            "ollama": await state.llm.health(),
            "browser": await state.browser_status(),
            "agent": state.agent_state(),
        }

    @app.get("/api/state")
    async def full_state(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return await state.snapshot()

    @app.get("/api/agent/status")
    async def agent_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return state.agent_state()

    # ---------- browser ----------

    @app.post("/api/browser/connect")
    async def browser_connect(
        payload: ConnectRequest, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        credentials = None
        if payload.http_username.strip() and payload.http_password:
            credentials = {
                "username": payload.http_username.strip(),
                "password": payload.http_password,
            }
        try:
            return await state.connect_browser(payload.url, http_credentials=credentials)
        except BrowserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/browser/disconnect")
    async def browser_disconnect(state: AppState = Depends(get_state)) -> dict[str, Any]:
        await state.disconnect_browser()
        return await state.browser_status(refresh=False)

    @app.get("/api/browser/status")
    async def browser_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return await state.browser_status()

    @app.post("/api/browser/check-login")
    async def browser_check_login(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return await state.browser_status()

    # ---------- Moodle sync ----------

    @app.post("/api/sync")
    async def sync_now(state: AppState = Depends(get_state)) -> dict[str, Any]:
        """Read courses, assignment status, and due dates without the model."""
        try:
            return state.start_sync("manual")
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- chat & events ----------

    @app.post("/api/chat")
    async def chat(payload: ChatRequest, state: AppState = Depends(get_state)) -> dict[str, Any]:
        try:
            conversation_id, run_id = await state.start_chat(
                payload.message, payload.conversation_id
            )
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"conversation_id": conversation_id, "run_id": run_id}

    @app.post("/api/agent/stop")
    async def stop_agent(state: AppState = Depends(get_state)) -> dict[str, Any]:
        stopped = await state.stop_agent()
        return {"ok": stopped, "agent": state.agent_state()}

    # ---------- data reset ----------

    @app.post("/api/reset")
    async def reset_data(state: AppState = Depends(get_state)) -> dict[str, Any]:
        """Clear courses, assignments, chats, activity, approvals and files."""
        try:
            return state.reset_local_data()
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/events")
    async def events(request: Request, state: AppState = Depends(get_state)) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            queue = state.bus.subscribe()
            try:
                light = await state.light_snapshot()
                yield f"data: {json.dumps({'type': 'snapshot', 'message': 'Connected', 'data': light}, ensure_ascii=False)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield event.to_sse()
            finally:
                state.bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------- approvals ----------

    @app.get("/api/approvals")
    async def approvals(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {"approvals": state.pending_approvals()}

    @app.post("/api/approvals/{approval_id}")
    async def resolve_approval(
        approval_id: str, payload: ApprovalDecision, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        resolved = state.resolve_approval(approval_id, payload.approved)
        if not resolved:
            raise HTTPException(status_code=404, detail="Approval not found or already resolved.")
        return {"ok": True, "approved": payload.approved}

    # ---------- Moodle data ----------

    @app.get("/api/courses")
    async def courses(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {"courses": state.db.list_courses()}

    @app.get("/api/assignments")
    async def assignments(
        course_id: int | None = Query(default=None),
        state: AppState = Depends(get_state),
    ) -> dict[str, Any]:
        rows = state.assignment_rows()
        if course_id is not None:
            rows = [row for row in rows if row.get("course_id") == course_id]
        return {"assignments": rows}

    # ---------- assignment drafts ----------

    @app.get("/api/assignments/{assignment_id}/draft")
    async def assignment_draft(
        assignment_id: int, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        """The agent's draft files for one assignment and their review state."""
        try:
            return state.assignment_draft(assignment_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/assignments/{assignment_id}/draft")
    async def start_draft(
        assignment_id: int, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        """Ask the agent to draft every deliverable. Nothing is submitted to Moodle."""
        try:
            return await state.start_draft(assignment_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/assignments/{assignment_id}/review")
    async def mark_reviewed(
        assignment_id: int, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        """Record the hash of every draft file as reviewed by the user."""
        try:
            return state.mark_draft_reviewed(assignment_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- files ----------

    @app.get("/api/workspace")
    async def workspace(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return state.workspace_tree()

    @app.get("/api/files/download")
    async def download_file(
        root: str = Query(pattern="^(workspace|generated)$"),
        path: str = Query(min_length=1),
        state: AppState = Depends(get_state),
    ) -> FileResponse:
        try:
            target = state.file_for_download(root, path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    # ---------- activity & conversations ----------

    @app.get("/api/activity")
    async def activity(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {"activity": state.db.list_activity(200)}

    @app.get("/api/conversations")
    async def conversations(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {"conversations": state.db.list_conversations(50)}

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(
        conversation_id: int, state: AppState = Depends(get_state)
    ) -> dict[str, Any]:
        if not state.db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {
            "conversation_id": conversation_id,
            "messages": state.conversation_messages(conversation_id),
        }

    @app.get("/api/tools")
    async def tools(state: AppState = Depends(get_state)) -> dict[str, Any]:
        return {"tools": state.tool_list()}

    return app


app = create_app()
