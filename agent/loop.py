"""The tool-calling agent loop.

One user turn may involve several model/tool rounds. Every tool call is
validated, classified, executed, and reported. The loop is bounded and honours
the user's Stop button between every step.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from agent.llm import LLMError, LLMResult, OllamaService, ToolCallRequest
from agent.prompts import build_draft_prompt, build_system_prompt
from app.config import Settings
from app.security import truncate_text
from browser.session import BrowserSession
from database.db import Database
from tools.base import ToolCancelled, ToolContext, ToolError, ToolRegistry

logger = logging.getLogger(__name__)

_FILE_TOOLS = frozenset({"create_word_document", "create_pdf", "create_code_file"})
_DRAFT_NUDGES = 3
_DRAFT_NUDGE = (
    "You have not created any deliverable files yet. Continue now by calling the tools: "
    "read the listed files if you have not, then create each deliverable with "
    "create_code_file, create_word_document, or create_pdf. Do not describe what you "
    "will do; call the tool."
)
_DRAFT_CONTINUE = (
    "Your last reply was empty. You have created {count} file(s) so far. If any task or "
    "deliverable from the brief is still missing, create it now with a tool call. When "
    "every deliverable exists, reply with the review checklist (Deliverables, "
    "Requirements, Check before submitting)."
)

EmitFn = Callable[[str, str, dict[str, Any] | None], None]
ApproveFn = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class AgentOutcome:
    content: str = ""
    cancelled: bool = False
    error: str | None = None
    steps: int = 0
    tool_calls: int = 0
    model: str = ""


@dataclass
class AgentRun:
    """Mutable per-turn state shared with the API layer."""

    conversation_id: int
    user_message: str
    # "chat" for a normal turn; "draft" adds the draft-mode rules to the prompt.
    mode: str = "chat"
    # Tool context state to start from, e.g. the assignment a draft run is for.
    seed_state: dict[str, Any] = field(default_factory=dict)
    draft_assignment_id: int | None = None
    stop_requested: bool = False
    outcome: AgentOutcome = field(default_factory=AgentOutcome)

    def request_stop(self) -> None:
        self.stop_requested = True


class MoodleAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        browser: BrowserSession,
        registry: ToolRegistry,
        llm: OllamaService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.browser = browser
        self.registry = registry
        self.llm = llm

    async def run(
        self,
        *,
        run: AgentRun,
        emit: EmitFn,
        approve: ApproveFn,
    ) -> AgentOutcome:
        ctx = ToolContext(
            settings=self.settings,
            db=self.db,
            browser=self.browser,
            jail=self._jail(),
            emit=emit,
            approve=approve,
            should_stop=lambda: run.stop_requested,
        )
        ctx.state.update(run.seed_state)

        history = list(
            self.db.get_recent_messages_for_prompt(run.conversation_id, limit=12)
        )
        if (
            not history
            or history[-1].get("role") != "user"
            or history[-1].get("content") != run.user_message
        ):
            history.append({"role": "user", "content": run.user_message})

        system_prompt = build_system_prompt(
            tool_names=self.registry.names(),
            workspace_root=str(self.settings.workspace_dir),
        )
        if run.mode == "draft":
            system_prompt += build_draft_prompt()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in history[-10:]:
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            messages.append(
                {"role": message["role"], "content": truncate_text(content, 6000)}
            )

        emit("status", "Thinking with qwen3:8b", {"model": self.settings.ollama_model})
        outcome = AgentOutcome(model=self.settings.ollama_model)
        files_created = 0
        nudges = 0

        for step in range(1, self.settings.max_agent_iterations + 1):
            if run.stop_requested:
                outcome.cancelled = True
                return await self._finish(run, outcome, emit, "Stopped by user")

            outcome.steps = step
            ctx.log("agent", f"Model round {step}", {"model": self.settings.ollama_model})

            try:
                result = await self.llm.stream_chat(
                    messages,
                    tools=self.registry.schemas(),
                    on_delta=lambda delta: emit("assistant_delta", delta, None),
                    should_stop=lambda: run.stop_requested,
                )
            except LLMError as exc:
                outcome.error = exc.message
                ctx.log("error", exc.message, {"kind": exc.kind})
                return await self._finish(run, outcome, emit, exc.message)
            except Exception as exc:
                logger.exception("Unexpected model failure")
                outcome.error = f"Unexpected model failure: {exc}"
                return await self._finish(run, outcome, emit, outcome.error)

            if result.cancelled or run.stop_requested:
                outcome.cancelled = True
                return await self._finish(run, outcome, emit, "Stopped by user")

            if result.content:
                emit("assistant_message", result.content, None)

            if (
                not result.tool_calls
                and run.mode == "draft"
                and (files_created == 0 or not result.content.strip())
                and nudges < _DRAFT_NUDGES
                and step < self.settings.max_agent_iterations
            ):
                # Small models often announce "I will retry" and then stop, or
                # return nothing halfway through. A draft is only useful once its
                # deliverables exist, so ask it to carry on instead of ending.
                nudges += 1
                if result.content:
                    messages.append({"role": "assistant", "content": result.content})
                if files_created == 0:
                    nudge = _DRAFT_NUDGE
                    reason = "No draft files yet; asking the model to continue"
                else:
                    nudge = _DRAFT_CONTINUE.format(count=files_created)
                    reason = "Empty model reply mid-draft; asking it to continue"
                messages.append({"role": "user", "content": nudge})
                ctx.log("agent", reason, None)
                continue

            if not result.tool_calls:
                outcome.content = result.content.strip()
                if not outcome.content:
                    # Reported once, by _finish.
                    outcome.error = (
                        "The model returned an empty response. Try rephrasing, or check that "
                        "qwen3:8b is running."
                    )
                return await self._finish(run, outcome, emit, outcome.content)

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    }
                    for call in result.tool_calls
                ],
            }
            messages.append(assistant_message)

            for call in result.tool_calls:
                if run.stop_requested:
                    outcome.cancelled = True
                    return await self._finish(run, outcome, emit, "Stopped by user")
                outcome.tool_calls += 1
                tool_payload = await self._execute_tool(ctx, call, emit, run)
                if call.name in _FILE_TOOLS and '"ok": true' in tool_payload:
                    files_created += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": call.name,
                        "content": tool_payload,
                    }
                )

        outcome.error = (
            f"Stopped after {self.settings.max_agent_iterations} tool rounds to avoid a loop. "
            "Ask for a smaller next step."
        )
        return await self._finish(run, outcome, emit, outcome.content)

    def _jail(self):
        from app.security import PathJail

        return PathJail(
            {
                "workspace": self.settings.workspace_dir,
                "generated": self.settings.generated_dir,
            }
        )

    async def _execute_tool(
        self,
        ctx: ToolContext,
        call: ToolCallRequest,
        emit: EmitFn,
        run: AgentRun,
    ) -> str:
        spec = self.registry.get(call.name)
        if spec is None:
            payload = {
                "ok": False,
                "error": f"Unknown tool {call.name!r}. Available tools: {self.registry.names()}",
            }
            emit("tool_result", f"Unknown tool: {call.name}", payload)
            return self._serialize(payload)

        emit(
            "tool_start",
            f"Using {call.name}",
            {"tool": call.name, "arguments": call.arguments},
        )
        ctx.log("tool", f"Using {call.name}", {"arguments": call.arguments})

        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            payload = {
                "ok": False,
                "error": "Invalid arguments for this tool.",
                "details": exc.errors(include_url=False),
            }
            emit("tool_result", f"Invalid arguments for {call.name}", payload)
            return self._serialize(payload)

        try:
            ctx.check_cancel()
            if spec.requires_approval:
                approved = await ctx.require_approval(
                    spec.name, {"arguments": call.arguments}
                )
                if not approved:
                    raise ToolError("The user denied this action.")
            data = await spec.handler(ctx, args)
            payload = {"ok": bool(data.get("ok", True)), **data}
            message = str(payload.get("message") or f"{call.name} completed")
        except ToolCancelled:
            raise
        except ToolError as exc:
            payload = {"ok": False, "error": exc.message, **exc.data}
            message = exc.message
        except Exception as exc:
            logger.exception("Tool %s failed", call.name)
            payload = {
                "ok": False,
                "error": f"{call.name} failed unexpectedly: {exc}",
            }
            message = payload["error"]

        emit("tool_result", message, payload)
        ctx.log("tool", message, {"tool": call.name, "ok": payload.get("ok")})
        return self._serialize(payload)

    def _serialize(self, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        limit = self.settings.max_tool_result_chars
        if len(text) <= limit:
            return text
        preview = truncate_text(text, max(0, limit - 300), marker="")
        return json.dumps(
            {
                "ok": payload.get("ok", False),
                "truncated": True,
                "message": payload.get("message") or payload.get("error") or "",
                "preview": preview,
            },
            ensure_ascii=False,
        )

    async def _finish(
        self,
        run: AgentRun,
        outcome: AgentOutcome,
        emit: EmitFn,
        text: str,
    ) -> AgentOutcome:
        run.outcome = outcome
        if outcome.content:
            try:
                self.db.add_message(run.conversation_id, "assistant", outcome.content)
            except Exception:
                logger.exception("Could not save assistant message")
        if outcome.cancelled:
            emit("stopped", "Agent stopped", {"steps": outcome.steps})
        elif outcome.error:
            emit("error", outcome.error, None)
        else:
            emit(
                "done",
                "Response complete",
                {
                    "steps": outcome.steps,
                    "tool_calls": outcome.tool_calls,
                    "model": outcome.model,
                },
            )
        return outcome
