"""Assignment drafts: review-state hashing and the "Draft with AI" API flow.

No browser and no Ollama: a scripted fake model writes one code file and then
returns its review checklist.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.llm import LLMResult, ToolCallRequest
from app.api import create_app
from app.config import load_settings
from app.drafts import draft_summary, input_files, record_review
from app.security import PathJail
from app.state import AppState

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"X-MoodleAI": "1"}
CHECKLIST = "## Deliverables\n- solution.py\n## Requirements\n- Sum two numbers -> solution.py"


class ScriptedLLM:
    """Writes solution.py with a tool call, then answers with the checklist."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def health(self) -> dict:
        return {"ok": True, "reachable": True, "model": "qwen3:8b", "installed": True}

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages]})
        if len(self.calls) == 1:
            return LLMResult(
                tool_calls=[
                    ToolCallRequest(
                        name="create_code_file",
                        arguments={"filename": "solution.py", "code": "print(1 + 2)"},
                    )
                ]
            )
        return LLMResult(content=CHECKLIST)

    async def aclose(self) -> None:
        return None


def _jail(tmp_path: Path) -> PathJail:
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "generated").mkdir(exist_ok=True)
    return PathJail({"workspace": tmp_path / "workspace", "generated": tmp_path / "generated"})


def test_draft_status_tracks_review_hashes(tmp_path: Path) -> None:
    jail = _jail(tmp_path)
    base = tmp_path / "workspace" / "Course" / "Lab 1"
    (base / "instructions").mkdir(parents=True)
    (base / "instructions" / "assignment.md").write_text("Do the lab", encoding="utf-8")
    (base / "instructions" / "metadata.json").write_text("{}", encoding="utf-8")
    (base / "attachments").mkdir()
    (base / "attachments" / "brief.pdf").write_bytes(b"%PDF-1.4")

    # Teacher files and instructions are inputs, never part of the draft.
    assert draft_summary(jail, base)["status"] == "none"
    # Instructions come first so the model reads the brief before the attachments.
    assert input_files(jail, base) == [
        "Course/Lab 1/instructions/assignment.md",
        "Course/Lab 1/attachments/brief.pdf",
    ]
    with pytest.raises(ValueError):
        record_review(jail, base)

    (base / "code").mkdir()
    (base / "code" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (base / "code" / "main.py.part").write_text("partial", encoding="utf-8")
    summary = draft_summary(jail, base)
    assert summary["status"] == "draft"
    assert [item["path"] for item in summary["files"]] == ["Course/Lab 1/code/main.py"]

    reviewed = record_review(jail, base)
    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_at"]

    (base / "code" / "main.py").write_text("print('edited')\n", encoding="utf-8")
    changed = draft_summary(jail, base)
    assert changed["status"] == "changed"
    assert changed["changed"] == ["Course/Lab 1/code/main.py"]

    record_review(jail, base)
    (base / "code" / "main.py").unlink()
    (base / "documents").mkdir()
    (base / "documents" / "report.docx").write_bytes(b"docx")
    removed = draft_summary(jail, base)
    assert removed["status"] == "changed"
    assert removed["changed"] == [
        "Course/Lab 1/code/main.py",
        "Course/Lab 1/documents/report.docx",
    ]


def test_inputs_skip_text_already_in_assignment_md(tmp_path: Path) -> None:
    jail = _jail(tmp_path)
    base = tmp_path / "workspace" / "Course" / "Lab"
    (base / "instructions").mkdir(parents=True)
    (base / "extracted").mkdir()
    (base / "attachments").mkdir()
    (base / "instructions" / "assignment.md").write_text(
        "<!-- attachment: tasks.pptx -->\nslides\n<!-- attachment: big.pdf -->\n"
        "…[truncated; full text in extracted/big.pdf.txt]",
        encoding="utf-8",
    )
    for name in ("page.txt", "tasks.pptx.txt", "big.pdf.txt"):
        (base / "extracted" / name).write_text("x", encoding="utf-8")
    (base / "attachments" / "data.csv").write_text("a,b", encoding="utf-8")

    assert input_files(jail, base) == [
        "Course/Lab/instructions/assignment.md",
        "Course/Lab/attachments/data.csv",
        "Course/Lab/extracted/big.pdf.txt",
    ]


def test_size_limits_are_clamped_not_rejected() -> None:
    from tools.document_tools import ReadDocumentArgs
    from tools.pdf_tools import ReadPdfArgs

    assert ReadDocumentArgs(path="a.md", max_chars=100000).max_chars == 60000
    assert ReadDocumentArgs(path="a.md", max_chars="10").max_chars == 1000
    assert ReadPdfArgs(path="a.pdf", max_pages=999, max_chars=5).max_pages == 200
    with pytest.raises(ValueError):
        ReadDocumentArgs(path="a.md", max_chars="lots")


@pytest.fixture()
def api(tmp_path: Path):
    settings = replace(
        load_settings(),
        project_root=tmp_path,
        browser_profile_dir=tmp_path / "browser" / "profile",
    )
    settings.ensure_directories()
    target_ui = tmp_path / "app" / "ui"
    target_ui.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copyfile(PROJECT_ROOT / "app" / "ui" / name, target_ui / name)

    llm = ScriptedLLM()
    state = AppState(settings, llm=llm)
    with TestClient(create_app(state), base_url="http://127.0.0.1") as client:
        yield client, state, settings, llm


def _wait_idle(client: TestClient) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if not client.get("/api/agent/status").json()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("agent run did not finish")


def test_draft_endpoints_require_header_and_known_assignment(api) -> None:
    client, _state, _settings, _llm = api

    assert client.post("/api/assignments/1/draft", json={}).status_code == 403
    assert client.post("/api/assignments/1/review", json={}).status_code == 403
    assert client.get("/api/assignments/999/draft").status_code == 404
    assert client.post("/api/assignments/999/draft", json={}, headers=HEADERS).status_code == 404


def test_draft_run_writes_files_into_the_assignment_folder(api) -> None:
    client, state, settings, llm = api
    course_id = state.db.upsert_course("Programming 101", "https://moodle.test/course/view.php?id=7")
    assignment_id = state.db.upsert_assignment(
        course_id, "Lab 1", "https://moodle.test/mod/assign/view.php?id=42", due_text="Friday"
    )
    instructions = settings.workspace_dir / "Programming 101" / "Lab 1" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "assignment.md").write_text("Write a program that adds 1 and 2.", encoding="utf-8")
    state.db.set_assignment_workspace(assignment_id, "Programming 101/Lab 1")

    before = client.get(f"/api/assignments/{assignment_id}/draft").json()
    assert before["status"] == "none"
    assert before["inputs"] == ["Programming 101/Lab 1/instructions/assignment.md"]
    assert client.post(f"/api/assignments/{assignment_id}/review", json={}, headers=HEADERS).status_code == 400

    started = client.post(f"/api/assignments/{assignment_id}/draft", json={}, headers=HEADERS)
    assert started.status_code == 200
    assert started.json()["workspace"] == "Programming 101/Lab 1"
    _wait_idle(client)

    # Draft mode adds its rules, and the request lists the saved instruction file.
    system = llm.calls[0]["messages"][0]["content"]
    assert "DRAFT MODE" in system
    request = llm.calls[0]["messages"][-1]["content"]
    assert "Programming 101/Lab 1/instructions/assignment.md" in request

    base = settings.workspace_dir / "Programming 101" / "Lab 1"
    assert (base / "code" / "solution.py").read_text(encoding="utf-8") == "print(1 + 2)\n"
    note = (base / "explanations" / "REVIEW.md").read_text(encoding="utf-8")
    assert "Sum two numbers -> solution.py" in note

    draft = client.get(f"/api/assignments/{assignment_id}/draft").json()
    assert draft["status"] == "draft"
    assert [item["path"] for item in draft["files"]] == ["Programming 101/Lab 1/code/solution.py"]
    assert draft["review_note"] == "Programming 101/Lab 1/explanations/REVIEW.md"
    download = client.get(draft["files"][0]["download_url"])
    assert download.status_code == 200

    reviewed = client.post(f"/api/assignments/{assignment_id}/review", json={}, headers=HEADERS)
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"

    (base / "code" / "solution.py").write_text("print(3)\n", encoding="utf-8")
    assert client.get(f"/api/assignments/{assignment_id}/draft").json()["status"] == "changed"

    # The draft is an ordinary conversation, so the user can follow up in chat.
    conversation_id = started.json()["conversation_id"]
    history = client.get(f"/api/conversations/{conversation_id}/messages").json()["messages"]
    assert [row["role"] for row in history] == ["user", "assistant"]


class StopsEarlyLLM(ScriptedLLM):
    """Writes one file, then only ever returns empty replies."""

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages]})
        if len(self.calls) == 1:
            return LLMResult(
                tool_calls=[
                    ToolCallRequest(
                        name="create_code_file",
                        arguments={"filename": "task1.py", "code": "print('task 1')"},
                    )
                ]
            )
        return LLMResult(content="")


def test_unfinished_draft_gets_an_honest_review_note(api) -> None:
    client, state, settings, _llm = api
    state.llm = StopsEarlyLLM()
    state.agent.llm = state.llm
    course_id = state.db.upsert_course("Python Lab", "https://moodle.test/course/view.php?id=9")
    assignment_id = state.db.upsert_assignment(
        course_id, "Lab 2", "https://moodle.test/mod/assign/view.php?id=77"
    )
    started = client.post(f"/api/assignments/{assignment_id}/draft", json={}, headers=HEADERS)
    assert started.status_code == 200
    _wait_idle(client)

    draft = client.get(f"/api/assignments/{assignment_id}/draft").json()
    assert draft["status"] == "draft"
    assert [item["name"] for item in draft["files"]] == ["task1.py"]
    note = (settings.workspace_dir / draft["review_note"]).read_text(encoding="utf-8")
    assert "Draft incomplete" in note
    assert "task1.py" in note
