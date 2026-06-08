"""Unit tests for agent_console (TASK-122, game-console prototype).

Covers the pure render functions (deterministic, no I/O) and the best-effort
loaders (must never raise, even on missing dirs / malformed files).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_console as ac  # noqa: E402


# ---------- render_panel (pure) ----------

def test_render_panel_has_box_borders():
    rows = ac.render_panel("Title", ["a", "b"], width=20)
    assert rows[0].startswith("┌─ Title ")
    assert rows[0].endswith("┐")
    assert rows[-1].startswith("└") and rows[-1].endswith("┘")
    # every row is the same display length (ASCII content)
    assert len({len(r) for r in rows}) == 1


def test_render_panel_empty_shows_none():
    rows = ac.render_panel("Empty", [], width=20)
    assert any("(none)" in r for r in rows)


def test_render_panel_clips_long_line():
    rows = ac.render_panel("T", ["x" * 100], width=20)
    body = rows[1]
    assert len(body) == 20
    assert "…" in body


def test_render_panel_widens_for_long_title():
    rows = ac.render_panel("A very long panel title", [], width=10)
    # width is bumped so the title fits + border
    assert rows[0].endswith("┐")
    assert "A very long panel title" in rows[0]


# ---------- render_console (pure) ----------

def test_render_console_contains_all_three_panels():
    out = ac.render_console(
        sessions=[{"role": "backend", "status": "active", "task_id": "TASK-1"}],
        messages=[{"from": "backend", "to": "qa", "type": "question",
                   "intent": "ping"}],
        tasks=[{"id": "TASK-9", "priority": "High", "owner": "QA"}],
        now="2026-05-27T00:00:00+09:00",
    )
    assert "Online Agents (1)" in out
    assert "Team Chat (1)" in out
    assert "Quest Board (1)" in out
    assert "backend" in out
    assert "backend→qa [question] ping" in out
    assert "[High] TASK-9 (QA)" in out
    assert "2026-05-27T00:00:00+09:00" in out


def test_render_console_empty_state():
    out = ac.render_console([], [], [], now="t")
    assert "no live sessions" in out
    assert "no recent messages" in out
    assert "no waiting TASKs" in out
    assert "Online Agents (0)" in out


def test_render_console_is_deterministic():
    args = ([{"role": "qa", "status": "idle", "task_id": "none"}], [], [], 60, "t")
    assert ac.render_console(*args) == ac.render_console(*args)


# ---------- loaders never raise ----------

def test_load_sessions_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "SESSIONS_DIR", tmp_path / "nope")
    assert ac.load_sessions() == []


def test_load_sessions_skips_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "SESSIONS_DIR", tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "good.json").write_text(
        json.dumps({"agent_id": "agent_x", "role": "backend",
                    "status": "active", "task_id": "TASK-1"}),
        encoding="utf-8",
    )
    out = ac.load_sessions()
    assert len(out) == 1
    assert out[0]["role"] == "backend"


def test_load_recent_messages_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "INBOX_DIR", tmp_path / "nope")
    assert ac.load_recent_messages() == []


def test_load_recent_messages_reads_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "INBOX_DIR", tmp_path)
    (tmp_path / "MSG-20260527-100000-aaaaaa.md").write_text(
        "---\nid: MSG-20260527-100000-aaaaaa\nfrom: backend\nto: qa\n"
        "task_id: TASK-1\nintent: hello\ntype: question\nstatus: open\n"
        "ts: 2026-05-27T10:00:00+09:00\n---\nbody\n",
        encoding="utf-8",
    )
    out = ac.load_recent_messages()
    assert len(out) == 1
    assert out[0]["from"] == "backend"
    assert out[0]["intent"] == "hello"


def test_load_recent_messages_limit_keeps_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "INBOX_DIR", tmp_path)
    for i in range(5):
        (tmp_path / f"MSG-20260527-10000{i}-aaaaa{i}.md").write_text(
            f"---\nid: MSG-20260527-10000{i}-aaaaa{i}\nfrom: a\nto: b\n"
            f"task_id: none\nintent: msg{i}\ntype: request\nstatus: open\n"
            f"ts: 2026-05-27T10:00:0{i}+09:00\n---\n",
            encoding="utf-8",
        )
    out = ac.load_recent_messages(limit=2)
    assert len(out) == 2
    assert out[-1]["intent"] == "msg4"   # latest by filename sort


def test_load_waiting_tasks_filters_and_sorts(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "TASKS_DIR", tmp_path)
    (tmp_path / "TASK-001-a.md").write_text(
        "---\nid: TASK-001\nstatus: 대기\npriority: Low\nowner: QA\n---\n",
        encoding="utf-8")
    (tmp_path / "TASK-002-b.md").write_text(
        "---\nid: TASK-002\nstatus: 대기\npriority: Critical\nowner: Backend\n---\n",
        encoding="utf-8")
    (tmp_path / "TASK-003-c.md").write_text(
        "---\nid: TASK-003\nstatus: 완료\npriority: High\nowner: QA\n---\n",
        encoding="utf-8")
    out = ac.load_waiting_tasks()
    ids = [t["id"] for t in out]
    assert ids == ["TASK-002", "TASK-001"]   # Critical first, 완료 excluded


# ---------- CLI ----------

def test_cli_once_prints_snapshot(capsys):
    rc = ac.main(["--width", "60"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AGENT CONSOLE" in out
    assert "Online Agents" in out


# ---------- TASK-126 collaboration panel ----------

def test_render_console_collab_panel_present():
    out = ac.render_console(
        [], [], [], now="t",
        collabs=[{"task_id": "TASK-048", "tier": "T0", "method": "reviewer",
                  "verdict": "reject", "tokens": 38000}],
    )
    assert "Collaboration (1)" in out
    assert "TASK-048 [T0/reviewer] -> reject 38000tok" in out


def test_render_console_collab_omitted_when_none():
    out = ac.render_console([], [], [], now="t")
    assert "Collaboration" not in out


def test_render_console_collab_empty_state():
    out = ac.render_console([], [], [], now="t", collabs=[])
    assert "Collaboration (0)" in out
    assert "no collaborations yet" in out


def test_load_recent_collabs_reads_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "EVENTS_DIR", tmp_path)
    (tmp_path / "collab-2026-05-27.jsonl").write_text(
        '{"task_id":"TASK-1","tier":"T0","method":"reviewer","verdict":"approve","tokens":100}\n'
        '{"not":"valid\n'   # malformed line skipped
        '{"task_id":"TASK-2","tier":"T1","method":"skeptic","verdict":"reject"}\n',
        encoding="utf-8",
    )
    out = ac.load_recent_collabs()
    assert len(out) == 2
    assert out[0]["task_id"] == "TASK-1"
    assert out[1]["verdict"] == "reject"


def test_load_recent_collabs_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "EVENTS_DIR", tmp_path / "nope")
    assert ac.load_recent_collabs() == []
