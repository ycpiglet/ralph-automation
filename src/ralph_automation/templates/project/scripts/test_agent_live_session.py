"""Unit tests for agent_live_session (TASK-127) + agent_console --exit-on-stop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_live_session as ls  # noqa: E402
import agent_console as ac  # noqa: E402


# ---------- build_session_plan (pure) ----------

def test_plan_console_has_two_panes():
    plan = ls.build_session_plan("console")
    assert plan.observe == "console"
    assert plan.work_cmd
    assert "agent_console.py" in " ".join(plan.observe_cmd)
    assert "--watch" in plan.observe_cmd


def test_plan_console_exit_on_stop_wires_stopfile():
    plan = ls.build_session_plan("console", exit_on_stop=True)
    assert "--exit-on-stop" in plan.observe_cmd
    assert "--stop-file" in plan.observe_cmd


def test_plan_no_exit_on_stop_omits_flag():
    plan = ls.build_session_plan("console", exit_on_stop=False)
    assert "--exit-on-stop" not in plan.observe_cmd


def test_plan_pipeline_mode_uses_observer():
    plan = ls.build_session_plan("pipeline", pipeline="build")
    assert "agent_observer.py" in " ".join(plan.observe_cmd)
    assert "--pipeline" in plan.observe_cmd
    assert "build" in plan.observe_cmd


def test_plan_rejects_bad_observe():
    with pytest.raises(ValueError):
        ls.build_session_plan("hologram")


def test_plan_notes_describe_game_flow():
    plan = ls.build_session_plan("console")
    joined = " ".join(plan.notes)
    assert "소환" in joined
    assert "자동 사라짐" in joined
    assert "중간 개입" in joined


def test_render_plan_includes_panes():
    out = ls.render_plan(ls.build_session_plan("console"))
    assert "work pane" in out
    assert "observe" in out
    assert "exit-on-stop" in out


# ---------- CLI ----------

def test_stop_file_is_dedicated_not_loop():
    # TASK-127 reviewer #4: must NOT share agent_loop's STOP_LOOP
    assert ls.STOP_FILE.name == "STOP_LIVE_SESSION"


def test_launch_argv_puts_yes_before_command(monkeypatch):
    # TASK-127 reviewer #3: --command is REMAINDER, --yes must precede it
    captured = {}

    class _R:
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _R()

    monkeypatch.setattr(ls.subprocess, "run", fake_run)
    plan = ls.build_session_plan("console")
    ls.launch_via_terminal(plan, yes=True)
    argv = captured["argv"]
    yi = argv.index("--yes")
    ci = argv.index("--command")
    assert yi < ci, "--yes must come before --command (REMAINDER)"
    # observe_cmd passed as separate tokens after --command
    assert argv[ci + 1] == plan.observe_cmd[0]


def test_cli_preview_console(capsys):
    rc = ls.main(["preview", "--observe", "console"])
    assert rc == 0
    assert "LIVE SESSION PLAN" in capsys.readouterr().out


def test_cli_stop_and_resume(capsys, tmp_path, monkeypatch):
    stop = tmp_path / "STOP_LOOP"
    monkeypatch.setattr(ls, "STOP_FILE", stop)
    rc = ls.main(["stop"])
    assert rc == 0
    assert stop.exists()
    rc2 = ls.main(["resume"])
    assert rc2 == 0
    assert not stop.exists()


# ---------- agent_console --exit-on-stop ----------

def test_console_exit_on_stop_returns_immediately(capsys, tmp_path, monkeypatch):
    stop = tmp_path / "STOP_LOOP"
    stop.write_text("x", encoding="utf-8")
    # watch + exit-on-stop + existing stop-file => return 0 without looping
    rc = ac.main(["--watch", "--exit-on-stop", "--stop-file", str(stop), "--width", "40"])
    assert rc == 0
    assert "stop-file 감지" in capsys.readouterr().out
