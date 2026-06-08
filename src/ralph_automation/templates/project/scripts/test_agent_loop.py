"""Unit tests for agent_loop (TASK-070).

Tests for stop conditions, mode dispatch, dry-run preview output,
and event log structure.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_loop  # noqa: E402


# --- stop conditions ---

def test_max_iterations_stops_after_n(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    text = out.getvalue()
    assert "[iteration 1]" in text
    assert "[iteration 2]" not in text
    assert "max_iterations reached (1)" in text


def test_stop_file_halts_immediately(tmp_path):
    stop = tmp_path / "STOP_LOOP"
    stop.write_text("halt")
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=5, allow_dirty=True, stop_file=stop,
    )
    out = io.StringIO()
    rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    assert "stop file present" in out.getvalue()
    assert "[iteration 1]" not in out.getvalue()


def test_dirty_worktree_blocks_without_allow_dirty(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=False,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "is_worktree_dirty", return_value=(True, "dirty stub")):
        rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    assert "dirty worktree" in out.getvalue()
    assert "[iteration 1]" not in out.getvalue()


def test_allow_dirty_overrides_dirty_block(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "is_worktree_dirty", return_value=(True, "dirty stub")):
        rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    assert "[iteration 1]" in out.getvalue()


# --- mode dispatch ---

def test_plan_mode_produces_preview_actions(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "READ agents/lead_engineer/STATUS.md" in text
    assert "PROPOSE next TASK" in text


def test_build_mode_dry_run_lists_orchestrator_calls(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="build", max_iterations=1, allow_dirty=True, dry_run=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "safety_gate.check_emergency_stop" in text
    assert "agent_orchestrator" in text
    assert "agent_worker" in text


# test_build_mode_non_dry_run_skips_first_cut — removed.
# TASK-115 잔여 구현 후 build non-dry-run 이 실제 orchestrator /status 호출.
# replacement: test_build_non_dry_run_calls_orchestrator_status / _marks_failed_*.


# test_scaffolded_modes_marked_skipped — removed.
# TASK-115 잔여 구현 후 retro 도 실제 동작. 모든 5 모드 구현 완료.
# scaffolded_skip 패턴 자체는 run_mode_not_implemented 가 보존 (미래 새 모드 추가 시).


# --- TASK-115 review mode ---

def test_review_mode_returns_ok_when_merges_present(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="review", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    fake_merges = [
        {"sha": "abc1234", "subject": "feat: thing (#42)", "files": ["scripts/test_foo.py"]},
    ]
    with patch.object(agent_loop, "list_recent_merges", return_value=fake_merges):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "1 recent merge(s) scanned" in text
    assert "abc1234" in text


def test_review_mode_no_merges_still_ok(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="review", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "list_recent_merges", return_value=[]):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "no recent merges found" in text


def test_review_mode_surfaces_collab_gate(tmp_path):
    """CYCLE-073: review mode surfaces cycle_gate collaboration for the current diff."""
    import cycle_gate
    cfg = agent_loop.LoopConfig(
        mode="review", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    with patch.object(cycle_gate, "_git_changed", return_value=["Managed database/x.sql"]), \
         patch.object(agent_loop, "list_recent_merges", return_value=[]):
        result = agent_loop.run_mode_review(cfg, 1)
    assert result.status == "ok"
    assert "collab gate: 등급 Critical" in result.detail
    assert "auditor" in result.detail          # required subagent
    assert "independent-auditor" in result.detail  # required worker /call
    assert any("collaboration gate" in a for a in result.actions)


def test_review_mode_collab_gate_degrades_gracefully(tmp_path):
    """A cycle_gate failure must not crash review mode (read-only, never-raise)."""
    import cycle_gate
    cfg = agent_loop.LoopConfig(
        mode="review", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    def _boom(_base):
        raise RuntimeError("git unavailable")
    with patch.object(cycle_gate, "_git_changed", side_effect=_boom), \
         patch.object(agent_loop, "list_recent_merges", return_value=[]):
        result = agent_loop.run_mode_review(cfg, 1)
    assert result.status == "ok"
    assert "평가 불가" in result.detail


def test_risk_score_low_for_small_test_only_change():
    merge = {"sha": "x", "subject": "s", "files": ["scripts/test_foo.py"]}
    level, reasons = agent_loop.risk_score(merge)
    assert level == "low"
    assert "has test changes" in reasons


def test_risk_score_high_for_many_files_with_sensitive():
    files = ["AGENTS.md"] + [f"src/file_{i}.py" for i in range(25)]
    merge = {"sha": "x", "subject": "s", "files": files}
    level, reasons = agent_loop.risk_score(merge)
    assert level == "high"
    assert any("sensitive" in r for r in reasons)


def test_risk_score_medium_for_no_tests_no_sensitive():
    merge = {"sha": "x", "subject": "s", "files": ["scripts/regular.py"]}
    level, reasons = agent_loop.risk_score(merge)
    assert level == "medium"
    assert "no test changes" in reasons


# --- TASK-115 audit mode ---

def test_audit_mode_summarizes_lint_output(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="audit", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "run_check_agent_docs",
                      return_value=(0, 0, 3, "OK: 0 error(s), 0 warning(s)")):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "lint clean" in text
    assert "3 info(s)" in text


def test_audit_mode_marks_failed_on_errors(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="audit", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "run_check_agent_docs",
                      return_value=(2, 1, 0, "FAILED: 2 error(s), 1 warning(s)")):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=failed" in text
    assert "lint fail" in text
    assert "2 error(s)" in text


# --- TASK-115 잔여: build non-dry-run ---

def test_build_non_dry_run_calls_orchestrator_status(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="build", max_iterations=1, allow_dirty=True, dry_run=False,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    fake_completed = type("R", (), {"returncode": 0,
                                     "stdout": "active=0 open=1 claimed=0\n",
                                     "stderr": ""})()
    with patch("subprocess.run", return_value=fake_completed):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "non-dry-run" in text
    assert "orchestrator /status OK" in text


def test_build_non_dry_run_marks_failed_on_orchestrator_error(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="build", max_iterations=1, allow_dirty=True, dry_run=False,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    fake_failed = type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
    with patch("subprocess.run", return_value=fake_failed):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=failed" in text
    assert "exit 1" in text


# --- TASK-213 build mode write-back dispatch (opt-in) ---

def _open_msg(inbox, name="MSG-20260603-070000-aaaaaa.md", to="qa"):
    p = inbox / name
    p.write_text(
        "---\n"
        f"id: {name[:-3]}\n"
        "from: backend\n"
        f"to: {to}\n"
        "type: question\n"
        "status: open\n"
        "ts: 2026-06-03T07:00:00+09:00\n"
        "---\n"
        "do the thing\n",
        encoding="utf-8",
    )
    return p


def test_build_dry_run_with_dispatch_shows_preview(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="build", max_iterations=1, allow_dirty=True, dry_run=True,
        dispatch=True, stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "DISPATCH auto_dispatch" in text
    assert "write_back=True" in text


def test_build_dispatch_default_off_keeps_deferral(tmp_path):
    # Without --dispatch the dry-run preview must NOT mention dispatch (behavior preserved).
    cfg = agent_loop.LoopConfig(
        mode="build", max_iterations=1, allow_dirty=True, dry_run=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    agent_loop.run_loop(cfg, out=out)
    assert "DISPATCH auto_dispatch" not in out.getvalue()


def test_run_build_dispatch_writes_back(tmp_path, monkeypatch):
    import agent_worker
    from agent_worker import parse_frontmatter
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    monkeypatch.setattr(agent_worker, "MESSAGES_INBOX", tmp_path)
    msg = _open_msg(tmp_path)
    cfg = agent_loop.LoopConfig(mode="build", dispatch=True,
                                dispatch_provider="dummy", dispatch_max=5)
    line = agent_loop._run_build_dispatch(cfg)
    assert "dispatched=1" in line and "replied=1" in line
    # the Ralph loop actually closed the message lifecycle
    assert parse_frontmatter(msg.read_text(encoding="utf-8"))[0]["status"] == "answered"


def test_run_build_dispatch_empty_inbox(tmp_path, monkeypatch):
    import agent_worker
    monkeypatch.setattr(agent_worker, "MESSAGES_INBOX", tmp_path)
    cfg = agent_loop.LoopConfig(mode="build", dispatch=True)
    assert "no open inbox work items" in agent_loop._run_build_dispatch(cfg)


def test_run_build_dispatch_live_blocked_does_not_crash(tmp_path, monkeypatch):
    # A live provider without the env gate must surface as "blocked", not crash
    # the loop, and must not dispatch (message stays open — claim never reached).
    import agent_worker
    from agent_worker import parse_frontmatter
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    monkeypatch.setattr(agent_worker, "MESSAGES_INBOX", tmp_path)
    msg = _open_msg(tmp_path)
    cfg = agent_loop.LoopConfig(mode="build", dispatch=True, dispatch_provider="claude")
    line = agent_loop._run_build_dispatch(cfg)
    assert "blocked" in line
    assert parse_frontmatter(msg.read_text(encoding="utf-8"))[0]["status"] == "open"


# --- TASK-115 잔여: retro mode ---

def test_retro_mode_returns_ok_when_no_retros(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="retro", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "latest_retro_path", return_value=None):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "no RETRO" in text


def test_retro_mode_summarizes_forward_items(tmp_path):
    fake_retro = tmp_path / "RETRO-test.md"
    fake_retro.write_text("dummy", encoding="utf-8")
    cfg = agent_loop.LoopConfig(
        mode="retro", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    fake_items = [
        {"kind": "TASK 후보", "proposal": "Stage 7 multi-llm spec foobar", "priority": "High"},
        {"kind": "TASK 후보", "proposal": "agent loop retro mode example", "priority": "Medium"},
        {"kind": "Compound 후보", "proposal": "self-review pattern", "priority": ""},
    ]
    out = io.StringIO()
    with patch.object(agent_loop, "latest_retro_path", return_value=fake_retro), \
         patch.object(agent_loop, "parse_retro_forward", return_value=fake_items), \
         patch.object(agent_loop, "count_unregistered_tasks", return_value=1):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "3 forward item(s)" in text
    assert "2 TASK candidate(s)" in text
    assert "1 unregistered" in text


def test_retro_mode_empty_forward_section(tmp_path):
    fake_retro = tmp_path / "RETRO-empty.md"
    fake_retro.write_text("dummy", encoding="utf-8")
    cfg = agent_loop.LoopConfig(
        mode="retro", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    with patch.object(agent_loop, "latest_retro_path", return_value=fake_retro), \
         patch.object(agent_loop, "parse_retro_forward", return_value=[]):
        agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert "status=ok" in text
    assert "empty or missing" in text


# --- TASK-115 잔여: 회복 정책 ---

def test_loop_halts_when_max_failures_reached(tmp_path):
    cfg = agent_loop.LoopConfig(
        mode="audit", max_iterations=5, max_failures=2, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    out = io.StringIO()
    # Force audit to fail every iteration → triggers max_failures halt after 2.
    with patch.object(agent_loop, "run_check_agent_docs",
                      return_value=(3, 0, 0, "FAILED: 3 error(s)")):
        rc = agent_loop.run_loop(cfg, out=out)
    text = out.getvalue()
    assert rc == 1  # halt exit code
    assert "max_failures reached (2/2)" in text
    assert "auto-stop" in text
    assert "[iteration 1]" in text
    assert "[iteration 2]" in text
    assert "[iteration 3]" not in text  # halted before 3rd


# --- CLI ---

def test_cli_help_does_not_crash(capsys):
    with pytest.raises(SystemExit) as exc_info:
        agent_loop.main(["--help"])
    assert exc_info.value.code == 0


def test_cli_requires_mode():
    with pytest.raises(SystemExit):
        agent_loop.main([])


def test_cli_rejects_unknown_mode(capsys):
    with pytest.raises(SystemExit):
        agent_loop.main(["--mode", "unknown"])


# --- event log ---

def test_event_log_writes_loop_start_and_stop(tmp_path, monkeypatch):
    fake_events = tmp_path / "events"
    monkeypatch.setattr(agent_loop, "EVENTS_DIR", fake_events)
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
    )
    agent_loop.run_loop(cfg, out=io.StringIO())
    log_files = list(fake_events.glob("agent_loop-*.jsonl"))
    assert len(log_files) == 1
    events = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines()]
    event_names = [e["event"] for e in events]
    assert "loop_start" in event_names
    assert "iteration_done" in event_names
    assert "loop_stop" in event_names
    for ev in events:
        assert "ts" in ev


def test_iteration_result_render_includes_actions():
    result = agent_loop.IterationResult(
        iteration=1, mode="plan", status="ok", detail="d",
        actions=["a1", "a2"],
    )
    text = agent_loop.render_iteration(result)
    assert "[iteration 1]" in text
    assert "mode=plan" in text
    assert "status=ok" in text
    assert "- a1" in text
    assert "- a2" in text


# --- TASK-117: heartbeat / backoff / stop-aware sleep ---


def test_heartbeat_written_every_iteration(tmp_path):
    hb = tmp_path / "heartbeat.json"
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=2, allow_dirty=True,
        stop_file=tmp_path / "no-stop", heartbeat_file=hb, heartbeat_interval=1,
    )
    out = io.StringIO()
    rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    assert hb.exists()
    record = json.loads(hb.read_text(encoding="utf-8"))
    assert record["mode"] == "plan"
    assert record["status"] in {"iteration_done", "stopped"}
    assert record["iteration"] >= 1


def test_heartbeat_disabled_when_interval_zero(tmp_path):
    hb = tmp_path / "heartbeat.json"
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=1, allow_dirty=True,
        stop_file=tmp_path / "no-stop", heartbeat_file=hb, heartbeat_interval=0,
    )
    out = io.StringIO()
    agent_loop.run_loop(cfg, out=out)
    assert not hb.exists()


def test_backoff_seconds_returns_capped_exponential():
    assert agent_loop.backoff_seconds(1, cap=10) == 2.0
    assert agent_loop.backoff_seconds(2, cap=10) == 4.0
    assert agent_loop.backoff_seconds(3, cap=10) == 8.0
    assert agent_loop.backoff_seconds(4, cap=10) == 10.0  # capped
    assert agent_loop.backoff_seconds(1, cap=0) == 0.0     # disabled
    assert agent_loop.backoff_seconds(0, cap=10) == 0.0    # no failure -> no sleep


def test_backoff_sleeps_on_failure(tmp_path):
    """When a handler raises, backoff sleep fires using cfg.sleeper."""
    calls: list[float] = []
    def fake_handler(_cfg, _it):
        raise RuntimeError("boom")
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=2, max_failures=5, allow_dirty=True,
        stop_file=tmp_path / "no-stop",
        heartbeat_file=tmp_path / "hb.json", heartbeat_interval=0,
        backoff_max_seconds=8, sleeper=lambda s: calls.append(s),
    )
    out = io.StringIO()
    with patch.dict(agent_loop.MODE_HANDLERS, {"plan": fake_handler}):
        agent_loop.run_loop(cfg, out=out)
    # iteration 1 failure -> 2s backoff, iteration 2 failure -> 4s backoff
    # sleep chunks of 0.5s: 2.0s -> 4 chunks; 4.0s -> 8 chunks
    assert sum(calls) == pytest.approx(6.0, rel=0.01)
    text = out.getvalue()
    assert "backoff: sleeping" in text


def test_stop_file_during_backoff_halts_loop(tmp_path):
    """A stop file appearing during backoff sleep ends the loop immediately."""
    stop = tmp_path / "STOP_LOOP"
    def fake_handler(_cfg, _it):
        raise RuntimeError("boom")
    sleep_calls: list[float] = []
    def fake_sleep(s):
        sleep_calls.append(s)
        # On the first chunk after iteration 1 failure, drop a stop file.
        if len(sleep_calls) == 1:
            stop.write_text("halt")
    cfg = agent_loop.LoopConfig(
        mode="plan", max_iterations=5, max_failures=10, allow_dirty=True,
        stop_file=stop, heartbeat_file=tmp_path / "hb.json",
        heartbeat_interval=0, backoff_max_seconds=8, sleeper=fake_sleep,
    )
    out = io.StringIO()
    with patch.dict(agent_loop.MODE_HANDLERS, {"plan": fake_handler}):
        rc = agent_loop.run_loop(cfg, out=out)
    assert rc == 0
    assert "stop file detected during backoff" in out.getvalue()
    # Only one chunk should have been slept before stop was detected.
    assert len(sleep_calls) == 1


def test_stop_aware_sleep_returns_true_when_stop_file_present(tmp_path):
    stop = tmp_path / "STOP_LOOP"
    stop.write_text("x")
    cfg = agent_loop.LoopConfig(
        mode="plan", stop_file=stop,
        heartbeat_file=tmp_path / "hb.json", heartbeat_interval=0,
        sleeper=lambda s: None,
    )
    assert agent_loop.stop_aware_sleep(cfg, 0) is True
    assert agent_loop.stop_aware_sleep(cfg, 1.0) is True


def test_loop_safety_caps_clamp_unbounded_iteration_and_budget():
    cfg = agent_loop.LoopConfig(
        mode="build",
        max_iterations=999,
        allow_dirty=True,
        dispatch=True,
        dispatch_session_budget=999_999,
    )

    capped, notes = agent_loop.apply_loop_safety_caps(cfg)

    assert capped.max_iterations == agent_loop.HARD_MAX_ITERATIONS
    assert capped.dispatch_session_budget == agent_loop.HARD_DISPATCH_SESSION_BUDGET
    assert any("max_iterations" in note for note in notes)
    assert any("dispatch_session_budget" in note for note in notes)


def test_explicit_auth_promotes_single_loop_to_bounded_default():
    cfg = agent_loop.LoopConfig(
        mode="build",
        max_iterations=1,
        allow_dirty=True,
        explicit_auth=True,
        goal="끝까지 정리",
    )

    capped, notes = agent_loop.apply_loop_safety_caps(cfg)

    assert capped.max_iterations == agent_loop.EXPLICIT_AUTH_DEFAULT_ITERATIONS
    assert capped.max_iterations <= agent_loop.HARD_MAX_ITERATIONS
    assert any("explicit_auth" in note for note in notes)
