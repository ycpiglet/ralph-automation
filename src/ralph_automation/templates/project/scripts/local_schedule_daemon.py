#!/usr/bin/env python3
"""User-session schedule daemon fallback.

Windows Task Scheduler can fail before the repo wrapper starts when the task is
registered in a different principal/logon context. This daemon keeps the same
R1 schedule path alive inside the current user session: it reads SCHEDULE.yml,
runs due notify schedules once per minute, and records heartbeat state under
schedule_runs/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import auto_runner
import schedule as schedule_mod

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RUN_DIR = ROOT / "schedule_runs"
STATE_PATH = RUN_DIR / "local_daemon.state.json"
LOG_PATH = RUN_DIR / "local_daemon.log"
STOP_PATH = RUN_DIR / "local_daemon.stop"


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def _minute_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _values_for(field: str, low: int, high: int, *, dow: bool = False) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = max(1, int(raw_step))
        if part == "*":
            start, end = low, high
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(part)
        for value in range(start, end + 1, step):
            if dow and value == 7:
                values.add(0)
            elif low <= value <= high:
                values.add(value)
    return values


def _field_matches(field: str, value: int, low: int, high: int, *, dow: bool = False) -> bool:
    return value in _values_for(field, low, high, dow=dow)


def is_due(cron: str, when: datetime) -> bool:
    fields = cron.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, dow = fields
    cron_dow = (when.weekday() + 1) % 7  # Python Mon=0; cron Sun=0/7.
    return (
        _field_matches(minute, when.minute, 0, 59)
        and _field_matches(hour, when.hour, 0, 23)
        and _field_matches(day, when.day, 1, 31)
        and _field_matches(month, when.month, 1, 12)
        and _field_matches(dow, cron_dow, 0, 7, dow=True)
    )


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("last_runs", {})
        return data
    except Exception:
        return {"last_runs": {}}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_log(message: str, log_path: Path = LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def _due_ids(entries: list[dict], when: datetime, state: dict, *, force: bool = False) -> list[str]:
    enabled = [entry for entry in entries if entry.get("enabled")]
    if force:
        return sorted(str(entry.get("id")) for entry in enabled if entry.get("id"))
    minute_key = _minute_key(when)
    last_runs = state.setdefault("last_runs", {})
    due: list[str] = []
    for entry in enabled:
        sid = str(entry.get("id") or "")
        if not sid:
            continue
        if is_due(str(entry.get("cron") or ""), when) and last_runs.get(sid) != minute_key:
            due.append(sid)
    return sorted(due)


def tick(*, now: datetime | None = None, force: bool = False, state: dict | None = None,
         state_path: Path = STATE_PATH, report_dir: Path | None = None) -> dict:
    when = now or _now()
    current = state if state is not None else load_state(state_path)
    current["pid"] = os.getpid()
    current.setdefault("started_at", _iso(when))
    current["last_heartbeat"] = _iso(when)
    current["last_tick"] = _minute_key(when)

    ids = _due_ids(schedule_mod.read_schedules(), when, current, force=force)
    result: dict
    if ids:
        result = auto_runner.from_schedule_run(schedule_ids=set(ids), report_dir=report_dir)
        if not result.get("halted"):
            for sid in ids:
                current.setdefault("last_runs", {})[sid] = _minute_key(when)
    else:
        result = {"halted": False, "ran": [], "reason": "no due schedules"}

    summary = {
        "time": _iso(when),
        "ran_ids": ids,
        "halted": bool(result.get("halted")),
        "reason": result.get("reason"),
        "report_path": result.get("report_path"),
    }
    current["last_result"] = summary
    save_state(current, state_path)
    _append_log(f"{summary['time']} tick ran={','.join(ids) or '-'} halted={summary['halted']}")
    return summary


def render_status(state: dict | None = None) -> str:
    current = state if state is not None else load_state()
    last = current.get("last_result") or {}
    lines = [
        "[local schedule daemon]",
        f"  state: {'present' if current else 'none'}",
        f"  pid={current.get('pid', '-')}",
        f"  started={current.get('started_at', '-')}",
        f"  heartbeat={current.get('last_heartbeat', '-')}",
        f"  last tick={current.get('last_tick', '-')}",
        f"  last ran={', '.join(last.get('ran_ids') or []) or '-'}",
        f"  stop file={'present' if STOP_PATH.exists() else 'absent'}",
    ]
    if last.get("report_path"):
        lines.append(f"  report={last['report_path']}")
    return "\n".join(lines)


def watch(interval: float, *, run_now: bool = False, max_ticks: int | None = None) -> int:
    state = load_state()
    state["pid"] = os.getpid()
    state["started_at"] = _iso(_now())
    ticks = 0
    if run_now:
        tick(force=True, state=state)
        ticks += 1
    while max_ticks is None or ticks < max_ticks:
        if STOP_PATH.exists():
            _append_log(f"{_iso(_now())} stop file present; exiting")
            break
        tick(state=state)
        ticks += 1
        time.sleep(interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local user-session schedule daemon fallback")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="show local daemon heartbeat")
    p_tick = sub.add_parser("tick", help="run one due check")
    p_tick.add_argument("--force", action="store_true", help="run all enabled notify schedules once")
    p_watch = sub.add_parser("watch", help="run continuously in this user session")
    p_watch.add_argument("--interval", type=float, default=60.0)
    p_watch.add_argument("--run-now", action="store_true", help="run all enabled schedules once before polling")
    p_watch.add_argument("--max-ticks", type=int, help="test/smoke bound")
    sub.add_parser("stop", help="write schedule_runs/local_daemon.stop")
    sub.add_parser("clear-stop", help="remove schedule_runs/local_daemon.stop")
    args = parser.parse_args(argv)

    cmd = args.cmd or "status"
    if cmd == "status":
        print(render_status())
        return 0
    if cmd == "tick":
        result = tick(force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if cmd == "watch":
        return watch(args.interval, run_now=args.run_now, max_ticks=args.max_ticks)
    if cmd == "stop":
        STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text(_iso(_now()) + "\n", encoding="utf-8")
        print(f"wrote {STOP_PATH}")
        return 0
    if cmd == "clear-stop":
        try:
            STOP_PATH.unlink()
            print(f"removed {STOP_PATH}")
        except FileNotFoundError:
            print(f"already absent: {STOP_PATH}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
