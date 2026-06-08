"""TASK-227 OS 트리거 대시보드 + CRUD 테스트.

순수 렌더는 직접, OS/PS 호출은 monkeypatch — 비-Windows CI 에서도 통과.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_schedule_task", ROOT / "scripts" / "schedule_task.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load()


# ---- render_board (pure) ----

def test_render_board_registered():
    out = st.render_board(
        {"available": True, "registered": True, "nextRun": "2026-06-05 07:53",
         "lastResult": 0, "lastRun": "2026-06-04 20:05"},
        [{"id": "daily-digest", "cron": "0 8 * * *", "selector": "digest", "mode": "notify", "enabled": True},
         {"id": "weekday-maintenance", "cron": "0 9 * * 1-5", "selector": "maintenance", "mode": "notify", "enabled": False}],
        {"mtime": "2026-06-04 20:05", "bottom_line": "Bottom Line: 열린 작업 10건"})
    assert "등록됨" in out
    assert "활성 1/2건" in out
    assert "OK(0)" in out
    assert "열린 작업 10건" in out
    assert "2026-06-05 07:53" in out


def test_render_board_registered_without_details():
    # schtasks 로 등록은 확정했지만 PowerShell 상세 enrich 생략(compact/hook 경로)
    out = st.render_board({"available": True, "registered": True}, [], None)
    assert "등록됨" in out
    assert "상세" in out  # 상세는 status 로 안내


def test_render_board_not_registered_shows_register_hint():
    out = st.render_board({"available": True, "registered": False}, [], None)
    assert "미등록" in out
    assert "register" in out
    assert "아직 없음" in out  # latest=None


def test_render_board_non_windows_graceful():
    out = st.render_board({"available": False, "registered": False}, [], None)
    assert "Windows 전용" in out


def test_render_board_compact_omits_entry_list():
    schedules = [{"id": "a", "cron": "0 8 * * *", "selector": "digest", "mode": "notify", "enabled": True}]
    full = st.render_board({"available": True, "registered": False}, schedules, None, compact=False)
    compact = st.render_board({"available": True, "registered": False}, schedules, None, compact=True)
    assert "[ON ]" in full and "[ON ]" not in compact  # compact 는 엔트리 줄 생략
    assert "관리:" in full and "관리:" not in compact


def test_result_label():
    assert st._result_label(0) == "OK(0)"
    assert st._result_label(None) == "-"
    assert "1" in st._result_label(1)


# ---- latest_summary ----

def test_latest_summary_extracts_bottom_line(tmp_path):
    p = tmp_path / "latest.md"
    p.write_text("# 보고\n\nBottom Line: 테스트 한 줄\n## 본문\n...", encoding="utf-8")
    s = st.latest_summary(p)
    assert s is not None and s["bottom_line"] == "Bottom Line: 테스트 한 줄"


def test_latest_summary_missing_returns_none(tmp_path):
    assert st.latest_summary(tmp_path / "nope.md") is None


def test_task_scheduler_wrapper_uses_stable_python_and_log():
    body = (ROOT / "scripts" / "run_schedule_task.cmd").read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%\\Programs\\Python\\Python310\\python.exe" in body
    assert '"%PY%" scripts\\auto_runner.py --from-schedule --run' in body
    assert "set \"ROOT=%~dp0..\"" in body
    assert "set \"LOG=%ROOT%\\schedule_runs\\last_task.log\"" in body


# ---- CRUD non-Windows guard ----

def test_register_non_windows(monkeypatch):
    monkeypatch.setattr(st, "_is_windows", lambda: False)
    assert st.register()["ok"] is False


def test_query_non_windows(monkeypatch):
    monkeypatch.setattr(st, "_is_windows", lambda: False)
    q = st.query_os_task()
    assert q["available"] is False and q["registered"] is False


def test_register_uses_cmd_wrapper_and_battery_safe_settings(monkeypatch, tmp_path):
    wrapper = tmp_path / "run_schedule_task.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")
    calls = {"schtasks": [], "ps": []}

    monkeypatch.setattr(st, "_is_windows", lambda: True)
    monkeypatch.setattr(st, "WRAPPER", wrapper)
    monkeypatch.setattr(st, "_schtasks",
                        lambda *args: calls["schtasks"].append(args) or {"ok": True, "msg": "created"})
    monkeypatch.setattr(st, "_ps",
                        lambda script, timeout=12: calls["ps"].append(script) or (0, "OK"))

    res = st.register("07:53")
    assert res["ok"] is True
    create_args = calls["schtasks"][0]
    tr = create_args[create_args.index("/TR") + 1]
    assert "cmd.exe" in tr
    assert "/d /c" in tr
    assert str(wrapper) in tr
    assert calls["ps"]
    assert "New-ScheduledTaskAction" in calls["ps"][0]
    assert "-WorkingDirectory" in calls["ps"][0]
    assert "-AllowStartIfOnBatteries" in calls["ps"][0]
    assert "-DontStopIfGoingOnBatteries" in calls["ps"][0]
