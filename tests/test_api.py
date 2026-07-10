"""
Test suite for the autonomous docx agent.

Run with:  pytest -v
Uses FastAPI's TestClient, so no server needs to be running — this exercises
the app in-process. LLM_PROVIDER defaults to "mock" so these tests run
offline, deterministically, with no API key and no network access.
"""
import os

os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.agent import Agent
from app.llm_client import llm_client

client = TestClient(app)


# ---------------------------------------------------------------------------
# API-level tests
# ---------------------------------------------------------------------------
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "endpoints" in r.json()


def test_agent_rejects_empty_request():
    r = client.post("/agent", json={"request": "   "})
    assert r.status_code == 400


def test_agent_rejects_missing_field():
    r = client.post("/agent", json={})
    assert r.status_code == 422  # pydantic validation error


@pytest.mark.parametrize("request_text,expected_type", [
    ("Create a project plan for launching a new mobile banking app", "Project Plan"),
    ("Write meeting minutes for the weekly engineering sync", "Meeting Minutes"),
    ("Draft a technical design document for a chat microservice", "Technical Design Document"),
    ("Prepare an SOP for onboarding new warehouse staff", "Standard Operating Procedure"),
    ("Write a product specification for a habit tracking app", "Product Specification"),
    ("Create a business proposal for managed IT support", "Business Proposal"),
])
def test_agent_classifies_document_type(request_text, expected_type):
    r = client.post("/agent", json={"request": request_text})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("completed", "completed_with_errors")
    assert body["document_type"] == expected_type
    assert len(body["plan"]) > 0
    assert all(t["status"] in ("done", "failed") for t in body["plan"])


def test_agent_returns_downloadable_file():
    r = client.post("/agent", json={"request": "Write meeting minutes for a budget review"})
    assert r.status_code == 200
    body = r.json()

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(dl.content) > 1000  # a real docx, not an empty stub


def test_download_rejects_path_traversal():
    r = client.get("/download/..%2F..%2Fapp%2Fmain.py")
    assert r.status_code in (404, 400)


def test_download_missing_file_is_404():
    r = client.get("/download/does-not-exist.docx")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Agent-level tests (bypass HTTP, exercise the planning/execution loop directly)
# ---------------------------------------------------------------------------
def test_agent_plan_has_one_task_per_section_plus_pipeline_steps():
    agent = Agent()
    result = agent.run("Prepare a quarterly business report on customer churn")
    names = [t.name for t in result["plan"]]
    assert names[0] == "Analyze request and generate document outline"
    assert names[-2] == "Assemble content into Word document"
    assert names[-1] == "Generate final summary"
    # every section between the outline step and assembly is a "Draft section: ..." task
    section_tasks = names[1:-2]
    assert all(n.startswith("Draft section:") for n in section_tasks)
    assert len(section_tasks) >= 5


def test_self_check_revises_bad_draft():
    """
    Forces the first section draft to be low-quality (placeholder text) and
    verifies the agent's reflection step detects it and revises once.
    """
    original = llm_client.chat_json
    state = {"served_bad_draft": False}

    def patched(system, user, *, task_type, context):
        if (
            task_type == "section"
            and not state["served_bad_draft"]
            and context.get("heading") == "Executive Summary"
        ):
            state["served_bad_draft"] = True
            return {
                "heading": "Executive Summary",
                "paragraphs": ["TBD - lorem ipsum placeholder"],
                "bullets": [],
                "table": None,
            }
        return original(system, user, task_type=task_type, context=context)

    llm_client.chat_json = patched
    try:
        agent = Agent()
        result = agent.run("Create a business proposal for managed IT support")
    finally:
        llm_client.chat_json = original

    exec_summary_task = next(t for t in result["plan"] if "Executive Summary" in t.name)
    assert exec_summary_task.status == "done"
    assert "Revised after self-check" in (exec_summary_task.detail or "")


def test_agent_degrades_gracefully_when_section_generation_fails():
    """A single failing section must not abort the whole run."""
    original = llm_client.chat_json

    def patched(system, user, *, task_type, context):
        if task_type == "section" and context.get("heading") == "Executive Summary":
            raise RuntimeError("simulated LLM outage")
        return original(system, user, task_type=task_type, context=context)

    llm_client.chat_json = patched
    try:
        agent = Agent()
        result = agent.run("Create a business proposal for managed IT support")
    finally:
        llm_client.chat_json = original

    assert result["status"] == "completed_with_errors"
    failed = [t for t in result["plan"] if t.status == "failed"]
    assert len(failed) == 1
    assert "Executive Summary" in failed[0].name
    # the document should still have been produced despite the failed section
    assert os.path.isfile(result["output_path"])
