"""Unit tests for the Codex session subagent bridge (TASK-135)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_messages as cm  # noqa: E402
import codex_subagent_bridge as bridge  # noqa: E402
import subagent_council as sc  # noqa: E402
import subagent_dispatch as sd  # noqa: E402


def test_dispatch_packet_writes_packet_call_and_event(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "BRIDGE_DIR", tmp_path / "packets")
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    packet = bridge.create_dispatch_packet(
        role_id="reviewer",
        task_id="TASK-135",
        intent="review Codex bridge",
        emit_call=True,
    )
    path = bridge.BRIDGE_DIR / f"{packet['id']}.json"
    assert path.exists()
    assert packet["runtime"] == "codex-session"
    assert packet["execution"]["tool"] == "multi_agent_v1.spawn_agent"
    assert "REVIEWER subagent" in packet["prompt"]
    call = tmp_path / packet["call_message"]
    meta, err = cm.load_frontmatter(call)
    assert err == "" and meta is not None
    assert meta["type"] == "subagent_call"
    assert meta["to"] == "subagent-reviewer"


def test_record_reply_writes_reply_and_marks_call_answered(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "BRIDGE_DIR", tmp_path / "packets")
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    packet = bridge.create_dispatch_packet(
        role_id="auditor",
        task_id="TASK-135",
        intent="audit",
        emit_call=True,
    )
    result = bridge.record_reply(
        bridge_id=packet["id"],
        verdict="APPROVED",
        summary="no issues",
    )
    reply = tmp_path / result["reply_message"]
    meta, err = cm.load_frontmatter(reply)
    assert err == "" and meta is not None
    assert meta["type"] == "subagent_reply"
    assert meta["in_reply_to"] == Path(packet["call_message"]).stem
    call_text = (tmp_path / packet["call_message"]).read_text(encoding="utf-8")
    assert "status: answered" in call_text


def test_council_packet_and_record(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "BRIDGE_DIR", tmp_path / "packets")
    monkeypatch.setattr(sd, "MESSAGES_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(sd, "EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(sc, "MESSAGES_INBOX", tmp_path / "inbox")
    packet = bridge.create_council_packet(
        task_id="TASK-135",
        members=["reviewer", "skeptic"],
        intent="judge bridge",
        emit_calls=True,
    )
    assert set(packet["prompts"]) == {"reviewer", "skeptic"}
    assert len(packet["call_messages"]) == 2
    result = bridge.record_council(
        bridge_id=packet["id"],
        task_id=None,
        method=None,
        verdicts=[
            sc.Verdict("reviewer", "approve", "ok"),
            sc.Verdict("skeptic", "approve", "ok"),
        ],
    )
    assert result["final"] == "approved"
    assert len(result["parent_calls_marked_answered"]) == 2
    for call in packet["call_messages"]:
        call_text = (tmp_path / call["call_message"]).read_text(encoding="utf-8")
        assert "status: answered" in call_text
    consensus = tmp_path / result["consensus_message"]
    meta, err = cm.load_frontmatter(consensus)
    assert err == "" and meta is not None
    assert meta["type"] == "consensus"


def test_cli_dispatch_dry_run_json(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "BRIDGE_DIR", tmp_path / "packets")
    rc = bridge.main([
        "dispatch",
        "--role",
        "implementer",
        "--task-id",
        "TASK-135",
        "--intent",
        "implement",
        "--dry-run",
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"runtime": "codex-session"' in out
    assert not (tmp_path / "packets").exists()
