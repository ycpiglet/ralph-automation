"""Local schedule daemon tests.

The daemon is the user-session fallback when Windows Task Scheduler can register
but returns LastTaskResult=255 before the wrapper starts.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_local_schedule_daemon", ROOT / "scripts" / "local_schedule_daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _s(**kw):
    base = {
        "id": "daily-digest",
        "cron": "0 8 * * *",
        "selector": "digest",
        "mode": "notify",
        "budget": 20000,
        "enabled": True,
    }
    base.update(kw)
    return base


def test_cron_due_supports_star_range_and_weekday():
    d = _load()
    monday_9 = datetime(2026, 6, 8, 9, 0)  # Monday
    sunday_9 = datetime(2026, 6, 7, 9, 0)  # Sunday

    assert d.is_due("0 9 * * 1-5", monday_9)
    assert not d.is_due("0 9 * * 1-5", sunday_9)
    assert d.is_due("0 8 * * *", datetime(2026, 6, 7, 8, 0))
    assert not d.is_due("0 8 * * *", datetime(2026, 6, 7, 8, 1))


def test_tick_runs_due_entries_once_per_minute(tmp_path, monkeypatch):
    d = _load()
    calls = []
    now = datetime(2026, 6, 8, 9, 0)
    monkeypatch.setattr(d.schedule_mod, "read_schedules",
                        lambda: [_s(id="m", cron="0 9 * * 1-5", selector="maintenance"),
                                 _s(id="d", cron="0 8 * * *", selector="digest")])
    monkeypatch.setattr(d.auto_runner, "from_schedule_run",
                        lambda *, schedule_ids, report_dir=None: calls.append(set(schedule_ids)) or {
                            "halted": False,
                            "ran": [{"id": next(iter(schedule_ids)), "kind": "maintenance"}],
                            "report_path": str(tmp_path / "latest.md"),
                        })

    state = d.load_state(tmp_path / "state.json")
    first = d.tick(now=now, state=state, state_path=tmp_path / "state.json")
    second = d.tick(now=now, state=state, state_path=tmp_path / "state.json")

    assert first["ran_ids"] == ["m"]
    assert second["ran_ids"] == []
    assert calls == [{"m"}]


def test_tick_force_runs_all_enabled_entries(tmp_path, monkeypatch):
    d = _load()
    calls = []
    monkeypatch.setattr(d.schedule_mod, "read_schedules",
                        lambda: [_s(id="m", selector="maintenance"),
                                 _s(id="d", selector="digest"),
                                 _s(id="off", enabled=False)])
    monkeypatch.setattr(d.auto_runner, "from_schedule_run",
                        lambda *, schedule_ids, report_dir=None: calls.append(set(schedule_ids)) or {
                            "halted": False,
                            "ran": [],
                            "report_path": str(tmp_path / "latest.md"),
                        })

    result = d.tick(now=datetime(2026, 6, 7, 11, 0), force=True,
                    state=d.load_state(tmp_path / "state.json"),
                    state_path=tmp_path / "state.json")

    assert result["ran_ids"] == ["d", "m"]
    assert calls == [{"d", "m"}]


def test_tick_refreshes_stale_pid(tmp_path, monkeypatch):
    d = _load()
    monkeypatch.setattr(d.schedule_mod, "read_schedules", lambda: [])
    state = {"pid": 1, "last_runs": {}}

    d.tick(now=datetime(2026, 6, 7, 11, 0), state=state, state_path=tmp_path / "state.json")

    assert state["pid"] != 1


def test_render_status_reports_heartbeat(tmp_path):
    d = _load()
    state = {
        "pid": 1234,
        "started_at": "2026-06-07T10:00:00+09:00",
        "last_heartbeat": "2026-06-07T10:01:00+09:00",
        "last_result": {"ran_ids": ["daily-digest"]},
    }

    out = d.render_status(state)

    assert "local schedule daemon" in out
    assert "pid=1234" in out
    assert "daily-digest" in out
