"""Phase 3 integration tests against the local Ollama daemon.

Run explicitly with:  $env:RUN_OLLAMA_TESTS="1"; python -m pytest tests/test_ollama.py -q
They are skipped by default so the fast unit suite stays fast.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agent.llm import OllamaService
from app.config import load_settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OLLAMA_TESTS") != "1",
    reason="Set RUN_OLLAMA_TESTS=1 to call the local model",
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_course",
            "description": "Open a Moodle course page by name.",
            "parameters": {
                "type": "object",
                "properties": {"course_name": {"type": "string"}},
                "required": ["course_name"],
            },
        },
    }
]


async def _with_service(coro_factory):
    """One event loop, one service, always closed (httpx pools bind to a loop)."""
    service = OllamaService(load_settings())
    try:
        return await coro_factory(service)
    finally:
        await service.aclose()


def test_health_reports_qwen3() -> None:
    async def scenario(service: OllamaService):
        return await service.health()

    health = asyncio.run(_with_service(scenario))
    assert health["reachable"], health["error"]
    assert health["installed"], health["error"]
    assert health["model"] == "qwen3:8b"


def test_streaming_tool_call() -> None:
    async def scenario(service: OllamaService):
        return await service.stream_chat(
            [{"role": "user", "content": "Open my Database Systems course."}],
            tools=TOOLS,
            num_predict=80,
        )

    result = asyncio.run(_with_service(scenario))
    assert result.tool_calls, "expected qwen3:8b to request open_course"
    call = result.tool_calls[0]
    assert call.name == "open_course"
    assert call.arguments.get("course_name")


def test_streaming_text() -> None:
    async def scenario(service: OllamaService):
        deltas: list[str] = []
        result = await service.stream_chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            on_delta=deltas.append,
            num_predict=12,
            temperature=0.0,
        )
        return result, deltas

    result, deltas = asyncio.run(_with_service(scenario))
    assert result.content, "thinking must be disabled and visible text returned"
    assert deltas, "on_delta should receive streamed chunks"
    assert not result.cancelled
