"""Unit tests for subagent_dispatch (TASK-116).

Covers:
  - 5 standard roles registered
  - render_prompt includes role-specific system prompt + output contract
  - emit_call_message produces a frontmatter that passes check_messages.py
  - emit_reply_message links via in_reply_to and uses type=subagent_reply
  - emit_event appends valid JSON to subagent-YYYY-MM-DD.jsonl
  - get_default_subagents resolves roles.yml default_subagents field
  - CLI: --list-roles, --for-worker, --role + --emit-call dry-run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subagent_dispatch as sd  # noqa: E402
import check_messages as cm  # noqa: E402


def test_five_roles_registered():
    assert set(sd.list_roles()) == {
        "implementer",
        "reviewer",
        "auditor",
        "strategist",
        "skeptic",
    }


def test_get_role_unknown_raises():
    with pytest.raises(KeyError):
        sd.get_role("doesnotexist")


def test_render_prompt_includes_role_pieces():
    prompt = sd.render_prompt(
        role_id="reviewer",
        task_id="TASK-116",
        intent="review subagent_dispatch.py",
        context_packet_path="some/path.md",
    )
    assert "REVIEWER subagent" in prompt
    assert "VERDICT" in prompt  # reviewer's output contract
    assert "TASK-116" in prompt
    assert "some/path.md" in prompt


def test_render_prompt_includes_auto_model_routing():
    prompt = sd.render_prompt(
        role_id="reviewer",
        task_id="TASK-239",
        intent="find and list the routing integration points",
        grade="Medium",
        model="auto",
    )
    assert "Agent tool model: haiku" in prompt
    assert "policy_tier=sonnet" in prompt
    assert "signals=simple_lookup" in prompt


def test_render_prompt_defaults_to_auto_model_routing():
    prompt = sd.render_prompt(
        role_id="reviewer",
        task_id="TASK-239",
        intent="review routing integration",
        grade="High",
    )
    assert "## Model routing" in prompt
    assert "Agent tool model: sonnet" in prompt
    assert "policy_tier=sonnet" in prompt


def test_render_prompt_skeptic_has_severity():
    prompt = sd.render_prompt("skeptic", "TASK-116", "find holes")
    assert "SKEPTIC subagent" in prompt
    assert "severity" in prompt


def test_emit_call_message_dry_run_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path)
    path = sd.emit_call_message(
        role_id="reviewer",
        task_id="TASK-116",
        intent="review dispatch helper",
        dry_run=True,
    )
    assert path.parent == tmp_path
    assert not path.exists()  # dry_run does not write


def test_emit_call_message_writes_valid_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path)
    path = sd.emit_call_message(
        role_id="auditor",
        task_id="TASK-116",
        intent="independent audit of TASK-116",
        evidence=["scripts/subagent_dispatch.py"],
        next_items=["check frontmatter", "verify event log"],
    )
    assert path.exists()
    meta, err = cm.load_frontmatter(path)
    assert err == "" and meta is not None, err
    for field in cm.REQUIRED_FIELDS:
        assert field in meta, f"missing {field}"
    assert meta["type"] == "subagent_call"
    assert meta["status"] == "open"
    assert meta["to"] == "subagent-auditor"
    assert meta["task_id"] == "TASK-116"
    assert meta["evidence"] == ["scripts/subagent_dispatch.py"]
    assert "check frontmatter" in meta["next"]


def test_emit_reply_message_links_to_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path)
    parent = sd.emit_call_message(
        role_id="reviewer", task_id="TASK-116", intent="review"
    )
    parent_id = parent.stem
    reply = sd.emit_reply_message(
        parent_id=parent_id,
        role_id="reviewer",
        task_id="TASK-116",
        verdict="APPROVED",
        summary="no issues found",
    )
    meta, err = cm.load_frontmatter(reply)
    assert err == "" and meta is not None
    assert meta["type"] == "subagent_reply"
    assert meta["status"] == "answered"
    assert meta["in_reply_to"] == parent_id
    assert "APPROVED" in reply.read_text(encoding="utf-8")


def test_emit_event_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path)
    path = sd.emit_event(
        role_id="auditor",
        task_id="TASK-116",
        kind="dispatch",
        extra={"message_id": "MSG-20260526-220000-abcdef", "intent": "test"},
    )
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "dispatch"
    assert record["role"] == "auditor"
    assert record["task_id"] == "TASK-116"
    assert record["message_id"] == "MSG-20260526-220000-abcdef"


def test_emit_event_rejects_unknown_kind():
    with pytest.raises(ValueError):
        sd.emit_event(
            role_id="reviewer", task_id="TASK-116", kind="bogus", dry_run=True
        )


def test_get_default_subagents_qa_includes_reviewer():
    """qa worker's default_subagents must include reviewer per roles.yml."""
    defaults = sd.get_default_subagents("qa")
    assert "reviewer" in defaults


def test_get_default_subagents_unknown_role_returns_empty():
    assert sd.get_default_subagents("not-a-real-role") == []


def test_cli_list_roles(capsys):
    rc = sd.main(["--list-roles"])
    assert rc == 0
    out = capsys.readouterr().out
    for role in sd.list_roles():
        assert role in out


def test_cli_for_worker_qa(capsys):
    rc = sd.main(["--for-worker", "qa"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reviewer" in out


def test_cli_dispatch_dry_run(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    rc = sd.main(
        [
            "--role",
            "implementer",
            "--task-id",
            "TASK-116",
            "--intent",
            "implement dispatch helper",
            "--emit-call",
            "--dry-run",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "IMPLEMENTER subagent" in out
    assert "Agent tool model:" in out
    assert "would write" in out
    # dry-run must not create files
    assert not (tmp_path / "inbox").exists() or not any(
        (tmp_path / "inbox").iterdir()
    )


def test_cli_dispatch_dry_run_accepts_auto_model(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    rc = sd.main(
        [
            "--role", "reviewer",
            "--task-id", "TASK-239",
            "--intent", "investigate why routing failed",
            "--grade", "High",
            "--model", "auto",
            "--emit-call",
            "--dry-run",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Agent tool model: opus" in out
    assert "policy_tier=sonnet" in out


def test_emit_event_records_full_routing_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path)
    decision = sd.resolve_model_decision(
        "auto",
        grade="High",
        intent="investigate why routing failed",
    )
    path = sd.emit_event(
        role_id="reviewer",
        task_id="TASK-239",
        kind="dispatch",
        extra=sd.routing_event_fields(decision),
    )
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["routing_grade"] == "High"
    assert record["policy_model"] == "sonnet"
    assert record["selected_model"] == "opus"
    assert record["routing_signals"] == ["deep_reasoning"]
    assert record["routing_reason"]


def test_emit_call_message_records_routing_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path)
    decision = sd.resolve_model_decision(
        "auto",
        grade="Medium",
        intent="find and list routing files",
    )
    path = sd.emit_call_message(
        role_id="reviewer",
        task_id="TASK-239",
        intent="find and list routing files",
        routing=decision,
    )
    meta, err = cm.load_frontmatter(path)
    assert err == "" and meta is not None
    assert meta["routing_grade"] == "Medium"
    assert meta["policy_model"] == "sonnet"
    assert meta["selected_model"] == "haiku"


def test_cli_requires_role_task_intent(capsys):
    rc = sd.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "required" in err


# ---------- TASK-143: subagent cap tests (RETRO §5 / STAGE-7 §7) ----------


def test_counter_starts_at_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    assert sd.load_subagent_counter("TASK-999") == 0


def test_increment_counter_monotonic(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    assert sd.increment_subagent_counter("TASK-999") == 1
    assert sd.increment_subagent_counter("TASK-999") == 2
    assert sd.load_subagent_counter("TASK-999") == 2


def test_reset_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    sd.increment_subagent_counter("TASK-999")
    sd.increment_subagent_counter("TASK-999")
    sd.reset_subagent_counter("TASK-999")
    assert sd.load_subagent_counter("TASK-999") == 0


def test_cap_zero_means_unlimited(tmp_path, monkeypatch, capsys):
    """--max-subagents-per-task 0 (default) bypasses the cap entirely."""
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    # 3 dispatches with default cap=0 — all should succeed
    for _ in range(3):
        rc = sd.main([
            "--role", "reviewer",
            "--task-id", "TASK-CAPTEST",
            "--intent", "test cap zero",
            "--emit-call",
            "--dry-run",
        ])
        assert rc == 0
    # counter shouldn't have been incremented (cap=0 skips counter)
    assert sd.load_subagent_counter("TASK-CAPTEST") == 0


def test_cap_blocks_after_threshold(tmp_path, monkeypatch, capsys):
    """cap=1 allows first dispatch (dry-run no increment), but with non-dry-run increments and blocks second."""
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    # First dispatch (non-dry-run) — counter becomes 1
    rc1 = sd.main([
        "--role", "reviewer",
        "--task-id", "TASK-CAPLIM",
        "--intent", "first dispatch",
        "--emit-call",
        "--max-subagents-per-task", "1",
    ])
    assert rc1 == 0
    assert sd.load_subagent_counter("TASK-CAPLIM") == 1
    # Second dispatch — counter already at cap, should block
    rc2 = sd.main([
        "--role", "reviewer",
        "--task-id", "TASK-CAPLIM",
        "--intent", "second dispatch",
        "--emit-call",
        "--max-subagents-per-task", "1",
    ])
    assert rc2 == 2
    err = capsys.readouterr().err
    assert "subagent cap reached" in err
    assert "TASK-CAPLIM" in err


def test_reset_counter_via_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sd, "SUBAGENT_COUNTER_DIR", tmp_path / "counter")
    sd.increment_subagent_counter("TASK-CAPRESET")
    assert sd.load_subagent_counter("TASK-CAPRESET") == 1
    rc = sd.main(["--reset-counter", "--task-id", "TASK-CAPRESET"])
    assert rc == 0
    assert sd.load_subagent_counter("TASK-CAPRESET") == 0
