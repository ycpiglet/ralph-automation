#!/usr/bin/env python3
"""task_events — task 변경 이벤트 로그 (TASK-234, 구조화 TASK 레이어 ④ · Linear SyncAction류).

write(task_api.update_status 등) 시 append-only 이벤트를 emit → `tasks.events.jsonl`.
`{seq, ts, action, task_id, fields, actor}`. sync·audit·realtime 의 기반.

gitignore(per-machine append): 커밋하면 두 세션의 동시 append 가 git 머지 충돌(COMPOUND-033
류)을 일으키므로 로컬 append 로 둔다 — 진짜 cross-machine sync 는 중앙 append 서비스가 필요
(본 단계 범위 밖). reader 로 재생/조회.

사용:
  python scripts/task_events.py            # 최근 이벤트
  python scripts/task_events.py --tail 5
  python scripts/task_events.py --task TASK-231
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "tasks.events.jsonl"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_events(path: Path = EVENTS_PATH) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _next_seq(path: Path) -> int:
    evs = read_events(path)
    return (evs[-1]["seq"] + 1) if evs else 1


def append_event(action: str, task_id: str, fields: dict | None = None,
                 actor: str = "api", path: Path = EVENTS_PATH) -> dict:
    """append-only 이벤트 1건 기록 후 반환. seq 단조 증가, ts ISO8601(tz)."""
    event = {
        "seq": _next_seq(path),
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": action,          # insert | update | delete | status
        "task_id": task_id,
        "fields": fields or {},
        "actor": actor,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def events_for(task_id: str, path: Path = EVENTS_PATH) -> list[dict]:
    return [e for e in read_events(path) if e.get("task_id") == task_id]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="task 변경 이벤트 로그 (TASK-234)")
    ap.add_argument("--tail", type=int, default=20, help="최근 N건(기본 20)")
    ap.add_argument("--task", help="특정 TASK 의 이벤트만")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    evs = events_for(args.task) if args.task else read_events()
    evs = evs[-args.tail:]
    if args.json:
        print(json.dumps(evs, ensure_ascii=False, indent=2))
    elif not evs:
        print("(이벤트 없음)")
    else:
        for e in evs:
            print(f"  #{e['seq']} {e['ts']} {e['action']:7} {e['task_id']} {e.get('fields')} ({e.get('actor')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
