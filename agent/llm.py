"""Thin, typed wrapper around the local Ollama daemon.

Only the local endpoint from settings is used. Errors are translated into
``LLMError`` so the API and agent loop can show actionable messages instead of
raw tracebacks.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

import httpx
from ollama import AsyncClient, ResponseError

from app.config import Settings

logger = logging.getLogger(__name__)

DeltaCallback = Callable[[str], Awaitable[None] | None]
StopCheck = Callable[[], bool]


class LLMError(RuntimeError):
    """A model/daemon failure with a user-facing message."""

    def __init__(self, message: str, *, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class ToolCallRequest:
    """A normalized tool call requested by the model."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str | None = None


@dataclass
class LLMResult:
    """Accumulated result of one streaming completion."""

    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    thinking: str = ""
    model: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0
    total_duration_s: float = 0.0
    cancelled: bool = False


def _model_name(model: Any) -> str:
    return str(getattr(model, "model", None) or getattr(model, "name", "") or "")


def _normalize_tool_calls(raw_calls: Sequence[Any]) -> list[ToolCallRequest]:
    normalized: list[ToolCallRequest] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_calls:
        function = getattr(raw, "function", None)
        if function is None and isinstance(raw, dict):
            function = raw.get("function")
        if function is None:
            continue
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments")
        if not name:
            continue

        raw_arguments: str | None = None
        if isinstance(arguments, str):
            raw_arguments = arguments
            try:
                parsed = json.loads(arguments)
                arguments = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                arguments = {}
        elif arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            arguments = {"value": arguments}

        fingerprint = (str(name), json.dumps(arguments, sort_keys=True, default=str))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        normalized.append(
            ToolCallRequest(name=str(name), arguments=arguments, raw_arguments=raw_arguments)
        )
    return normalized


class OllamaService:
    """Async client for the configured local model."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = AsyncClient(
            host=settings.ollama_host,
            timeout=httpx.Timeout(settings.ollama_timeout_s, connect=10.0),
        )
    # ---------- health ----------

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.list()
        except ResponseError as exc:
            raise LLMError(f"Ollama rejected the request: {exc.error}", kind="daemon") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError, OSError) as exc:
            raise LLMError(
                f"Cannot reach Ollama at {self.settings.ollama_host}. Is the Ollama app running?",
                kind="connection",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("Ollama did not answer in time.", kind="timeout") from exc

        models = []
        for model in getattr(response, "models", []) or []:
            models.append(
                {
                    "name": _model_name(model),
                    "size_bytes": int(getattr(model, "size", 0) or 0),
                    "modified_at": str(getattr(model, "modified_at", "") or ""),
                }
            )
        return models

    async def health(self) -> dict[str, Any]:
        """Return a UI-friendly health snapshot; never raises."""
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except LLMError as exc:
            return {
                "ok": False,
                "reachable": False,
                "host": self.settings.ollama_host,
                "model": self.settings.ollama_model,
                "installed": False,
                "models": [],
                "error": exc.message,
                "error_kind": exc.kind,
                "latency_ms": None,
            }

        names = {m["name"] for m in models}
        installed = self.settings.ollama_model in names
        running: list[str] = []
        try:
            ps_response = await self._client.ps()
            running = [
                _model_name(m) for m in (getattr(ps_response, "models", []) or [])
            ]
        except Exception:  # ps is diagnostic only; never fail health because of it
            running = []

        return {
            "ok": installed,
            "reachable": True,
            "host": self.settings.ollama_host,
            "model": self.settings.ollama_model,
            "installed": installed,
            "models": models,
            "running": running,
            "error": None if installed else (
                f"Model {self.settings.ollama_model!r} is not installed. "
                f"Run: ollama pull {self.settings.ollama_model}"
            ),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    # ---------- chat ----------

    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        on_delta: DeltaCallback | None = None,
        should_stop: StopCheck | None = None,
        num_predict: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Stream a completion and accumulate text + tool calls.

        Ollama emits a complete tool call in one chunk; arguments arrive as a
        dict through the Python SDK. We still tolerate JSON-string arguments.
        """
        options: dict[str, Any] = {
            "temperature": (
                self.settings.ollama_temperature if temperature is None else temperature
            ),
            "num_ctx": self.settings.ollama_num_ctx,
        }
        if num_predict is not None:
            options["num_predict"] = num_predict

        kwargs: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": list(messages),
            "stream": True,
            "options": options,
            "keep_alive": self.settings.ollama_keep_alive,
        }
        if tools:
            kwargs["tools"] = list(tools)
        # qwen3 defaults to thinking when the flag is omitted; force the
        # configured mode so the UI and latency stay predictable.
        kwargs["think"] = self.settings.ollama_think

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        final: Any = None

        try:
            stream = await self._client.chat(**kwargs)
            async for part in stream:
                if should_stop is not None and should_stop():
                    logger.info("Generation cancelled by user")
                    return LLMResult(
                        content="".join(content_parts),
                        tool_calls=tool_calls,
                        thinking="".join(thinking_parts),
                        model=self.settings.ollama_model,
                        cancelled=True,
                    )

                message = part.message
                thinking = getattr(message, "thinking", None)
                if thinking:
                    thinking_parts.append(str(thinking))

                delta = getattr(message, "content", None)
                if delta:
                    content_parts.append(str(delta))
                    if on_delta is not None:
                        callback_result = on_delta(str(delta))
                        if hasattr(callback_result, "__await__"):
                            await callback_result

                if getattr(message, "tool_calls", None):
                    tool_calls.extend(_normalize_tool_calls(message.tool_calls))

                if getattr(part, "done", False):
                    final = part

        except ResponseError as exc:
            if getattr(exc, "status_code", None) == 404:
                raise LLMError(
                    f"Model {self.settings.ollama_model!r} is not installed. "
                    f"Run: ollama pull {self.settings.ollama_model}",
                    kind="model_missing",
                ) from exc
            raise LLMError(f"Ollama error: {exc.error}", kind="daemon") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError, OSError) as exc:
            raise LLMError(
                f"Lost connection to Ollama at {self.settings.ollama_host}. "
                "Check that the Ollama app is still running.",
                kind="connection",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama timed out after {self.settings.ollama_timeout_s}s. "
                "Try a shorter request or a larger OLLAMA_TIMEOUT_S.",
                kind="timeout",
            ) from exc

        total_duration_ns = int(getattr(final, "total_duration", 0) or 0) if final else 0
        return LLMResult(
            content="".join(content_parts).strip(),
            tool_calls=tool_calls,
            thinking="".join(thinking_parts).strip(),
            model=self.settings.ollama_model,
            prompt_eval_count=int(getattr(final, "prompt_eval_count", 0) or 0) if final else 0,
            eval_count=int(getattr(final, "eval_count", 0) or 0) if final else 0,
            total_duration_s=round(total_duration_ns / 1e9, 2),
        )

    async def complete_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        num_predict: int = 256,
    ) -> str:
        """Single non-streaming text completion, for short internal summaries."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        result = await self.stream_chat(messages, num_predict=num_predict, temperature=0.1)
        return result.content

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()
