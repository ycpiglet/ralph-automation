"""Unit tests for agent_observer (TASK-103).

The observer is a pure, read-only consumer of runtime files. These tests craft
event logs + inbox fixtures and assert the derived snapshot, then enforce the
read-only invariant (the observer mutates no file). No real worker runs — fully
deterministic and CI-safe.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_observer as obs  # noqa: E402


# ---------- helpers ----------

def ev(name: str, **fields) -> dict:
    rec = {"ts": "2026-05-24T01:00:00+09:00", "role": "qa", "event": name}
    rec.update(fields)
    return rec


def write_events(events_dir: Path, role: str, records: list[dict]) -> Path:
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / f"{role}-{obs.date_today()}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    return path


def write_msg(inbox: Path, msg_id: str, *, to: str, frm: str = "orchestrator",
              status: str = "open", mtype: str = "request",
              task_id: str = "none", intent: str = "do thing") -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{msg_id}.md"
    fm = (f"---\nid: {msg_id}\nfrom: {frm}\nto: {to}\ntask_id: {task_id}\n"
          f"intent: {intent}\ntype: {mtype}\nstatus: {status}\n---\nbody\n")
    path.write_text(fm, encoding="utf-8")
    return path


# ---------- status derivation ----------

def test_status_offline_no_events_no_inbox():
    snap = obs.derive_snapshot("qa", [], [])
    assert snap.status == "offline"


def test_status_idle_started_then_polling():
    events = [ev("worker_started", provider="dummy")]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "idle"


def test_status_working_claimed_not_answered():
    events = [
        ev("worker_started", provider="dummy"),
        ev("message_claimed", message_id="MSG-1"),
    ]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "working"


def test_status_idle_after_full_cycle():
    events = [
        ev("worker_started", provider="dummy"),
        ev("message_claimed", message_id="MSG-1"),
        ev("provider_called", provider="dummy", reply_chars=42),
        ev("reply_written", reply_id="MSG-2", in_reply_to="MSG-1"),
        ev("status_updated", message_id="MSG-1", to_status="answered"),
    ]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "idle"
    assert snap.provider == "dummy"
    assert snap.last_reply_chars == 42


def test_status_error_provider_error_after_claim():
    events = [
        ev("worker_started", provider="claude"),
        ev("message_claimed", message_id="MSG-1"),
        ev("provider_error", provider="claude",
           error_type="ProviderAuthError", error="no key"),
    ]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "error"
    assert "ProviderAuthError" in snap.last_error


def test_status_error_cleared_by_later_success():
    events = [
        ev("provider_error", provider="claude",
           error_type="ProviderTimeout", error="slow"),
        ev("message_claimed", message_id="MSG-2"),
        ev("provider_called", provider="claude", reply_chars=10),
        ev("reply_written", reply_id="MSG-3", in_reply_to="MSG-2"),
        ev("status_updated", message_id="MSG-2", to_status="answered"),
    ]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "idle"


def test_status_stopped_last_event_worker_stopped():
    events = [
        ev("worker_started", provider="dummy"),
        ev("worker_stopped", reason="stop_file"),
    ]
    snap = obs.derive_snapshot("qa", events, [])
    assert snap.status == "stopped"


def test_status_blocked_inbound_message_blocked():
    events = [ev("worker_started", provider="dummy")]
    inbox = [{"id": "MSG-9", "to": "qa", "from": "lead-engineer",
              "type": "request", "status": "blocked", "intent": "x", "task_id": "TASK-1"}]
    snap = obs.derive_snapshot("qa", events, inbox)
    assert snap.status == "blocked"


def test_current_task_and_latest_message_from_inbox():
    inbox = [
        {"id": "MSG-1", "to": "qa", "from": "lead-engineer", "type": "request",
         "status": "answered", "intent": "old", "task_id": "TASK-1"},
        {"id": "MSG-2", "to": "qa", "from": "ceo", "type": "request",
         "status": "open", "intent": "newest", "task_id": "TASK-7"},
    ]
    snap = obs.derive_snapshot("qa", [ev("worker_started")], inbox)
    assert snap.current_task == "TASK-7"
    assert snap.latest_message["id"] == "MSG-2"
    assert snap.latest_message["intent"] == "newest"


def test_replies_do_not_count_as_latest_inbound():
    inbox = [
        {"id": "MSG-1", "to": "qa", "from": "lead", "type": "request",
         "status": "open", "intent": "req", "task_id": "TASK-1"},
        {"id": "MSG-2", "to": "qa", "from": "lead", "type": "reply",
         "status": "open", "intent": "reply", "task_id": "none"},
    ]
    snap = obs.derive_snapshot("qa", [ev("worker_started")], inbox)
    assert snap.latest_message["id"] == "MSG-1"


# ---------- read helpers ----------

def test_read_events_parses_jsonl_skips_garbage(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    path = events_dir / f"qa-{obs.date_today()}.jsonl"
    path.write_text(
        json.dumps(ev("worker_started")) + "\n"
        + "{ broken json\n"
        + json.dumps(ev("message_claimed", message_id="MSG-1")) + "\n",
        encoding="utf-8",
    )
    records = obs.read_events(events_dir, "qa")
    assert [r["event"] for r in records] == ["worker_started", "message_claimed"]


def test_read_inbox_matches_to_and_from(tmp_path):
    inbox = tmp_path / "inbox"
    write_msg(inbox, "MSG-1", to="qa", frm="ceo")
    write_msg(inbox, "MSG-2", to="ceo", frm="qa")        # from qa -> included
    write_msg(inbox, "MSG-3", to="backend", frm="ceo")   # unrelated -> excluded
    msgs = obs.read_inbox(inbox, "qa")
    ids = {m["id"] for m in msgs}
    assert ids == {"MSG-1", "MSG-2"}


# ---------- read-only invariant (the core safety property) ----------

def _dir_hash(path: Path) -> dict:
    out = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_observer_never_writes(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    inbox = tmp_path / "inbox"
    write_events(events_dir, "qa", [
        ev("worker_started", provider="dummy"),
        ev("message_claimed", message_id="MSG-1"),
        ev("provider_called", provider="dummy", reply_chars=5),
    ])
    write_msg(inbox, "MSG-1", to="qa", task_id="TASK-3", intent="review")

    monkeypatch.setattr(obs, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(obs, "MESSAGES_INBOX", inbox)

    before = _dir_hash(tmp_path)
    snap = obs.build_snapshot("qa")
    after = _dir_hash(tmp_path)

    assert before == after, "observer must not create/modify/delete any file"
    assert snap.current_task == "TASK-3"
    assert snap.provider == "dummy"


def test_render_text_runs_and_includes_status():
    snap = obs.derive_snapshot("qa", [ev("worker_started")], [])
    text = obs.render_text(snap)
    assert "Agent Observer" in text
    assert "status" in text


# ---------- terminal integration ----------

def test_terminal_observer_command():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_terminal as term
    cmd = term.observer_agent_command("qa")
    assert str(term.AGENT_OBSERVER) in cmd
    assert "--watch" in cmd and "qa" in cmd


def test_terminal_observer_precedence_over_worker():
    import agent_terminal as term

    class Args:
        command = None
        observer = True
        worker = True
        provider = "dummy"

    cmd = term.args_to_command(Args(), "qa", "none")
    assert str(term.AGENT_OBSERVER) in cmd
    assert str(term.AGENT_WORKER) not in cmd


def test_terminal_worker_still_works_regression():
    import agent_terminal as term

    class Args:
        command = None
        observer = False
        worker = True
        provider = "dummy"

    cmd = term.args_to_command(Args(), "qa", "none")
    assert str(term.AGENT_WORKER) in cmd


# ---------- TASK-106: fs watcher (opt-in) ----------

import threading  # noqa: E402


def test_runtime_watcher_none_without_watchdog(monkeypatch):
    """watchdog import 실패 시 None → 폴링 fallback."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("watchdog"):
            raise ImportError("simulated: watchdog not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    logs = []
    observer = obs.start_runtime_watcher(threading.Event(), log_fn=logs.append)
    assert observer is None
    assert any("watchdog unavailable" in m for m in logs)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("watchdog") is None,
    reason="watchdog not installed",
)
def test_runtime_watcher_signals_on_change(tmp_path, monkeypatch):
    """inbox 또는 events 변경 시 signal set (실 watchdog, 두 디렉토리 구독)."""
    inbox = tmp_path / "inbox"
    events = tmp_path / "events"
    inbox.mkdir()
    events.mkdir()
    monkeypatch.setattr(obs, "MESSAGES_INBOX", inbox)
    monkeypatch.setattr(obs, "EVENTS_DIR", events)

    signal = threading.Event()
    observer = obs.start_runtime_watcher(signal)
    assert observer is not None
    try:
        (events / f"qa-{obs.date_today()}.jsonl").write_text("{}\n", encoding="utf-8")
        assert signal.wait(timeout=5.0), "watcher did not signal on events change"
    finally:
        observer.stop()
        observer.join(timeout=2.0)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("watchdog") is None,
    reason="watchdog not installed",
)
def test_runtime_watcher_is_read_only(tmp_path, monkeypatch):
    """watcher 가동 + build_snapshot 후에도 파일 미변경 (read-only 불변식 보존)."""
    inbox = tmp_path / "inbox"
    events = tmp_path / "events"
    inbox.mkdir()
    events.mkdir()
    write_events(events, "qa", [ev("worker_started", provider="dummy")])
    write_msg(inbox, "MSG-1", to="qa", task_id="TASK-9")
    monkeypatch.setattr(obs, "MESSAGES_INBOX", inbox)
    monkeypatch.setattr(obs, "EVENTS_DIR", events)

    before = _dir_hash(tmp_path)
    signal = threading.Event()
    observer = obs.start_runtime_watcher(signal)
    try:
        obs.build_snapshot("qa")
    finally:
        observer.stop()
        observer.join(timeout=2.0)
    assert _dir_hash(tmp_path) == before, "observer/watcher must not write any file"


# ---------- TASK-112: pipeline view ----------

def test_read_all_events_merges_roles(tmp_path):
    import agent_observer as ao
    ev = tmp_path / "events"
    ev.mkdir()
    (ev / "backend-2026-05-26.jsonl").write_text(
        '{"ts":"2026-05-26T10:00:00+09:00","role":"backend","event":"pipeline_advanced","pipeline":"build","kind":"request","to":"qa","to_stage":"review"}\n',
        encoding="utf-8")
    (ev / "qa-2026-05-26.jsonl").write_text(
        '{"ts":"2026-05-26T10:01:00+09:00","role":"qa","event":"pipeline_advanced","pipeline":"build","kind":"request","to":"ci-cd","to_stage":"commit"}\n',
        encoding="utf-8")
    recs = ao.read_all_events(ev, "2026-05-26")
    assert [r["role"] for r in recs] == ["backend", "qa"]


def test_read_pipeline_messages_filters_by_pipeline(tmp_path):
    import agent_observer as ao
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "m1.md").write_text(
        "---\nid: m1\nto: qa\ntype: request\nstatus: open\n"
        "pipeline: build\nstage: review\nloopbacks: 1\n---\nbody\n", encoding="utf-8")
    (inbox / "m2.md").write_text(
        "---\nid: m2\nto: qa\ntype: request\nstatus: open\n---\nno pipeline\n",
        encoding="utf-8")
    msgs = ao.read_pipeline_messages(inbox, "build")
    assert len(msgs) == 1 and msgs[0]["stage"] == "review" and msgs[0]["loopbacks"] == "1"


def _adv(role, to, stage, kind="request", ts="2026-05-26T10:00:00+09:00"):
    return {"ts": ts, "role": role, "event": "pipeline_advanced",
            "pipeline": "build", "kind": kind, "to": to, "to_stage": stage}


def test_pipeline_snapshot_running_with_active_stage():
    import agent_observer as ao
    events = [_adv("backend", "qa", "review")]
    messages = [{"type": "request", "status": "open", "pipeline": "build",
                 "stage": "review", "loopbacks": "0", "to": "qa"}]
    snap = ao.derive_pipeline_snapshot("build", events, messages)
    assert snap.status == "running"
    assert snap.active_stage == "review" and snap.active_role == "qa"
    assert len(snap.history) == 1 and snap.history[0]["to_stage"] == "review"


def test_pipeline_snapshot_complete():
    import agent_observer as ao
    events = [_adv("backend", "qa", "review"),
              _adv("ci-cd", "ceo", "commit", kind="complete", ts="2026-05-26T10:05:00+09:00")]
    snap = ao.derive_pipeline_snapshot("build", events, [])
    assert snap.status == "complete" and snap.active_stage is None


def test_pipeline_snapshot_loopbacks_and_halt():
    import agent_observer as ao
    events = [_adv("qa", "ceo", "review", kind="halt")]
    messages = [{"type": "request", "status": "answered", "pipeline": "build",
                 "stage": "implement", "loopbacks": "2", "to": "backend"}]
    snap = ao.derive_pipeline_snapshot("build", events, messages)
    assert snap.status == "halt" and snap.loopbacks == 2


def test_render_pipeline_text():
    import agent_observer as ao
    snap = ao.PipelineSnapshot(pipeline="build", status="running",
                               active_stage="review", active_role="qa", loopbacks=1,
                               history=[{"ts": "t", "from": "backend", "to": "qa",
                                         "to_stage": "review", "kind": "request"}])
    text = ao.render_pipeline(snap)
    assert "build" in text and "running" in text and "review" in text and "qa" in text
    assert "backend" in text


def test_pipeline_mode_once_json(tmp_path, monkeypatch, capsys):
    import agent_observer as ao
    monkeypatch.setattr(ao, "EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(ao, "MESSAGES_INBOX", tmp_path / "inbox")
    rc = ao.main(["--pipeline", "build", "--once", "--json"])
    assert rc == 0
    import json as _j
    out = _j.loads(capsys.readouterr().out)
    assert out["pipeline"] == "build" and "status" in out


def test_role_or_pipeline_required():
    import agent_observer as ao
    import pytest
    with pytest.raises(SystemExit):
        ao.main(["--once"])


def test_snapshot_is_terminal_role_and_pipeline():
    import agent_observer as ao
    assert ao.snapshot_is_terminal(ao.Snapshot(role="qa", status="stopped")) is True
    assert ao.snapshot_is_terminal(ao.Snapshot(role="qa", status="working")) is False
    assert ao.snapshot_is_terminal(ao.PipelineSnapshot(pipeline="build", status="complete")) is True
    assert ao.snapshot_is_terminal(ao.PipelineSnapshot(pipeline="build", status="halt")) is True
    assert ao.snapshot_is_terminal(ao.PipelineSnapshot(pipeline="build", status="running")) is False


def test_exit_on_stop_returns_when_worker_stopped(tmp_path, monkeypatch):
    import agent_observer as ao
    ev = tmp_path / "events"
    ev.mkdir()
    (ev / f"qa-{ao.date_today()}.jsonl").write_text(
        '{"ts":"2026-05-26T10:00:00+09:00","role":"qa","event":"worker_stopped","reason":"once"}\n',
        encoding="utf-8")
    monkeypatch.setattr(ao, "EVENTS_DIR", ev)
    monkeypatch.setattr(ao, "MESSAGES_INBOX", tmp_path / "inbox")
    # --watch + --exit-on-stop must emit once then return 0 (not hang) because
    # the last event is worker_stopped → status "stopped".
    rc = ao.main(["--role", "qa", "--watch", "--exit-on-stop", "--interval", "0.1"])
    assert rc == 0
