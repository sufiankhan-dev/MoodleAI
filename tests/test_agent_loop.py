"""Phase 6 tests: the tool-calling loop with a deterministic fake model.

No Ollama call is made here; this isolates loop/validation/approval behavior.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.llm import LLMResult, ToolCallRequest
from agent.loop import AgentRun, MoodleAgent
from app.config import load_settings
from database.db import Database
from tools.base import StrictArgs, ToolContext, ToolError, ToolRegistry, ToolSpec


class EchoArgs(StrictArgs):
    text: str = ""


class DangerArgs(StrictArgs):
    pass


class FakeLLM:
    def __init__(self, results: list[LLMResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], **kwargs})
        if not self.results:
            return LLMResult(content="No more fake results")
        return self.results.pop(0)


@pytest.fixture()
def base(tmp_path: Path):
    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "profile",
    )
    settings.ensure_directories()
    db = Database(tmp_path / "test.db")
    conversation_id = db.create_conversation("Test chat")
    db.add_message(conversation_id, "user", "Please use the echo tool.")
    registry = ToolRegistry()
    executed: list[str] = []

    async def echo(_ctx: ToolContext, args: EchoArgs) -> dict:
        executed.append(args.text)
        return {"ok": True, "message": "echoed", "text": args.text}

    async def danger(_ctx: ToolContext, _args: DangerArgs) -> dict:
        executed.append("danger")
        return {"ok": True, "message": "danger ran"}

    registry.register(
        ToolSpec(
            name="echo",
            description="Echo text",
            args_model=EchoArgs,
            handler=echo,
        )
    )
    registry.register(
        ToolSpec(
            name="danger",
            description="A consequential action",
            args_model=DangerArgs,
            handler=danger,
            read_only=False,
            requires_approval=True,
        )
    )

    yield settings, db, conversation_id, registry, executed
    db.close()


def _run(agent: MoodleAgent, run: AgentRun, events: list, approve=None):
    async def approve_yes(_action, _details):
        return True

    def emit(kind, message, data):
        events.append((kind, message, data))

    return asyncio.run(agent.run(run=run, emit=emit, approve=approve or approve_yes))


def test_tool_call_then_final_answer(base) -> None:
    settings, db, conversation_id, registry, executed = base
    fake = FakeLLM(
        [
            LLMResult(tool_calls=[ToolCallRequest(name="echo", arguments={"text": "hello"})]),
            LLMResult(content="All done."),
        ]
    )
    agent = MoodleAgent(
        settings=settings, db=db, browser=None, registry=registry, llm=fake
    )
    events: list = []
    outcome = _run(
        agent,
        AgentRun(conversation_id=conversation_id, user_message="Please use the echo tool."),
        events,
    )

    assert outcome.content == "All done."
    assert outcome.tool_calls == 1
    assert executed == ["hello"]

    second_messages = fake.calls[1]["messages"]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["tool_calls"][0]["function"]["name"] == "echo"
    assert second_messages[-1]["role"] == "tool"
    assert second_messages[-1]["tool_name"] == "echo"
    assert json.loads(second_messages[-1]["content"])["ok"] is True

    stored = db.get_messages(conversation_id)
    assert stored[-1]["content"] == "All done."
    assert any(kind == "done" for kind, _msg, _data in events)


def test_invalid_arguments_are_reported_to_model(base) -> None:
    settings, db, conversation_id, registry, executed = base
    fake = FakeLLM(
        [
            LLMResult(tool_calls=[ToolCallRequest(name="echo", arguments={"text": ["not", "a", "string"]})]),
            LLMResult(content="I will retry."),
        ]
    )
    agent = MoodleAgent(
        settings=settings, db=db, browser=None, registry=registry, llm=fake
    )
    outcome = _run(
        agent,
        AgentRun(conversation_id=conversation_id, user_message="Please use the echo tool."),
        [],
    )

    assert outcome.content == "I will retry."
    assert executed == []
    tool_message = fake.calls[1]["messages"][-1]
    assert json.loads(tool_message["content"])["ok"] is False


def test_approval_denial_blocks_tool(base) -> None:
    settings, db, conversation_id, registry, executed = base
    fake = FakeLLM(
        [
            LLMResult(tool_calls=[ToolCallRequest(name="danger", arguments={})]),
            LLMResult(content="The user said no."),
        ]
    )
    agent = MoodleAgent(
        settings=settings, db=db, browser=None, registry=registry, llm=fake
    )

    async def approve_no(_action, _details):
        return False

    outcome = _run(
        agent,
        AgentRun(conversation_id=conversation_id, user_message="Do the dangerous thing."),
        [],
        approve=approve_no,
    )

    assert outcome.content == "The user said no."
    assert executed == []
    tool_payload = json.loads(fake.calls[1]["messages"][-1]["content"])
    assert tool_payload["ok"] is False
    assert "denied" in tool_payload["error"].lower()


def test_unknown_tool_is_reported(base) -> None:
    settings, db, conversation_id, registry, executed = base
    fake = FakeLLM(
        [
            LLMResult(tool_calls=[ToolCallRequest(name="rm_rf", arguments={})]),
            LLMResult(content="I cannot do that."),
        ]
    )
    agent = MoodleAgent(
        settings=settings, db=db, browser=None, registry=registry, llm=fake
    )
    outcome = _run(
        agent,
        AgentRun(conversation_id=conversation_id, user_message="Delete everything."),
        [],
    )

    assert outcome.content == "I cannot do that."
    payload = json.loads(fake.calls[1]["messages"][-1]["content"])
    assert payload["ok"] is False
    assert "Unknown tool" in payload["error"]


def test_cancelled_result_is_not_saved_as_answer(base) -> None:
    settings, db, conversation_id, registry, _executed = base
    fake = FakeLLM([LLMResult(content="partial", cancelled=True)])
    agent = MoodleAgent(
        settings=settings, db=db, browser=None, registry=registry, llm=fake
    )
    outcome = _run(
        agent,
        AgentRun(conversation_id=conversation_id, user_message="Please use the echo tool."),
        [],
    )
    assert outcome.cancelled is True
    assert outcome.content == ""
