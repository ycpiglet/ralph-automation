"""Unit tests for qa_negotiation (TASK-119).

Covers:
  - emit_question writes a frontmatter that passes check_messages.py
  - emit_question requires question_for, from_role, task_id, intent
  - emit_answer requires a valid question MSG id and writes in_reply_to
  - emit_answer + check_messages.py reject answer without in_reply_to
  - list_open_questions returns inbox questions
  - CLI: ask / answer / list happy paths
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_negotiation as qa  # noqa: E402
import check_messages as cm  # noqa: E402


# ---------- emit_question ----------

def test_emit_question_writes_valid_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    path = qa.emit_question(
        from_role="backend",
        question_for="qa",
        task_id="TASK-119",
        intent="staging row-level policy migration validation needed",
        evidence=["agents/lead_engineer/tasks/TASK-119-bidirectional-negotiation.md"],
    )
    assert path.exists()
    meta, err = cm.load_frontmatter(path)
    assert err == "" and meta is not None
    for field in cm.REQUIRED_FIELDS:
        assert field in meta
    assert meta["type"] == "question"
    assert meta["status"] == "open"
    assert meta["from"] == "backend"
    assert meta["to"] == "qa"
    assert meta["question_for"] == "qa"
    assert meta["task_id"] == "TASK-119"


def test_emit_question_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    path = qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="dry-run test",
        dry_run=True,
    )
    assert not path.exists()


def test_emit_question_rejects_empty_required_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    with pytest.raises(ValueError):
        qa.emit_question(from_role="", question_for="qa",
                         task_id="TASK-119", intent="x")
    with pytest.raises(ValueError):
        qa.emit_question(from_role="backend", question_for="",
                         task_id="TASK-119", intent="x")
    with pytest.raises(ValueError):
        qa.emit_question(from_role="backend", question_for="qa",
                         task_id="", intent="x")
    with pytest.raises(ValueError):
        qa.emit_question(from_role="backend", question_for="qa",
                         task_id="TASK-119", intent="")


# ---------- emit_answer ----------

def test_emit_answer_links_to_question(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    q = qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="ping",
    )
    a = qa.emit_answer(
        question_msg_id=q.stem,
        from_role="qa", to_role="backend",
        task_id="TASK-119", intent="pong",
        body="qa role mask answer",
    )
    meta, err = cm.load_frontmatter(a)
    assert err == "" and meta is not None
    assert meta["type"] == "answer"
    assert meta["status"] == "answered"
    assert meta["from"] == "qa"
    assert meta["to"] == "backend"
    assert meta["in_reply_to"] == q.stem


def test_emit_answer_rejects_bad_msg_id():
    with pytest.raises(ValueError):
        qa.emit_answer(question_msg_id="not-a-msg-id",
                       from_role="qa", to_role="backend",
                       task_id="TASK-119", intent="x")


# ---------- check_messages.py lint integration ----------

def test_lint_rejects_question_without_question_for(tmp_path, monkeypatch):
    """Hand-write a malformed question and confirm check_messages flags it."""
    monkeypatch.setattr(cm, "MESSAGES_DIR", tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    bad = inbox / "MSG-20260527-100000-aaaaaa.md"
    bad.write_text(
        "---\n"
        "id: MSG-20260527-100000-aaaaaa\n"
        "from: backend\n"
        "to: qa\n"
        "task_id: TASK-119\n"
        "intent: missing question_for\n"
        "type: question\n"
        "status: open\n"
        "ts: 2026-05-27T10:00:00+09:00\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    errors, _ = cm.lint()
    assert errors >= 1


def test_lint_accepts_qa_negotiation_question(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path / "inbox")
    qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="lint-acceptance",
    )
    errors, _ = cm.lint()
    assert errors == 0


def test_lint_accepts_qa_negotiation_question_and_answer_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path / "inbox")
    q = qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="pair test",
    )
    qa.emit_answer(
        question_msg_id=q.stem,
        from_role="qa", to_role="backend",
        task_id="TASK-119", intent="reply",
    )
    errors, _ = cm.lint()
    assert errors == 0


# ---------- list_open_questions ----------

def test_list_open_questions_filters_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    q = qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="open-q",
    )
    # a non-question file should be ignored
    other = tmp_path / "MSG-20260527-110000-bbbbbb.md"
    other.write_text(
        "---\n"
        "id: MSG-20260527-110000-bbbbbb\n"
        "from: backend\n"
        "to: qa\n"
        "task_id: TASK-119\n"
        "intent: not a question\n"
        "type: request\n"
        "status: open\n"
        "ts: 2026-05-27T11:00:00+09:00\n"
        "---\n",
        encoding="utf-8",
    )
    out = qa.list_open_questions()
    assert q in out
    assert other not in out


# ---------- CLI ----------

def test_cli_ask_dry_run(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    rc = qa.main([
        "ask",
        "--from", "backend",
        "--question-for", "qa",
        "--task-id", "TASK-119",
        "--intent", "cli test",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would write" in out


def test_cli_answer_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    q = qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="cli round trip",
    )
    rc = qa.main([
        "answer",
        "--to", q.stem,
        "--from", "qa",
        "--to-role", "backend",
        "--task-id", "TASK-119",
        "--intent", "cli answer",
    ])
    assert rc == 0


def test_cli_list_subcommand(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "MESSAGES_INBOX", tmp_path)
    qa.emit_question(
        from_role="backend", question_for="qa",
        task_id="TASK-119", intent="listable",
    )
    rc = qa.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert ".md" in out
