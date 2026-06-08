#!/usr/bin/env python3
"""스케줄 레지스트리 CRUD (TASK-224, MEETING-2026-06-04-001).

agents/lead_engineer/SCHEDULE.yml 의 엔트리를 add/list/remove/update/enable/disable.
데이터 계층만 — 실행 없음(R2 안전). 실행기(auto_runner, TASK-225)가 이 레지스트리를 읽는다.

PyYAML 비의존(requirements.txt 에 없음 → CI clean env 에서 import 실패). repo 관례대로
flat scalar 스키마를 손수 파싱/직렬화한다(round-trip 은 이 모듈이 단독 통제).

사용:
  python scripts/schedule.py list
  python scripts/schedule.py add --id daily-digest --cron "0 9 * * *" --selector digest --mode notify --budget 20000
  python scripts/schedule.py update --id daily-digest --cron "0 8 * * 1-5"
  python scripts/schedule.py enable --id daily-digest
  python scripts/schedule.py disable --id daily-digest
  python scripts/schedule.py remove --id daily-digest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:  # Windows 콘솔(cp949)에서도 UTF-8 출력
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_PATH = ROOT / "agents" / "lead_engineer" / "SCHEDULE.yml"

VALID_MODES = ("notify", "pr", "auto")
FIELD_ORDER = ("id", "cron", "selector", "mode", "budget", "enabled")


# ---------- YAML I/O (flat scalar 스키마, 손수 파싱 — PyYAML 비의존) ----------

def _split_header(text: str) -> str:
    """`schedules:` 줄 직전까지의 헤더(주석 블록)를 반환한다."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip().startswith("schedules:"):
            return "\n".join(lines[:i]).rstrip()
    return text.rstrip()


def _coerce(key: str, raw: str):
    val = raw.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    if key == "budget":
        try:
            return int(val)
        except ValueError:
            return val
    if key == "enabled":
        return val.lower() == "true"
    return val


def read_schedules(path: Path = SCHEDULE_PATH) -> list[dict]:
    """SCHEDULE.yml 의 엔트리를 dict 리스트로. 파일/블록 없으면 빈 리스트."""
    if not path.exists():
        return []
    entries: list[dict] = []
    current: dict | None = None
    in_block = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0] if not raw_line.lstrip().startswith("#") else ""
        stripped = line.strip()
        if not in_block:
            if stripped.startswith("schedules:"):
                in_block = True
                rest = stripped[len("schedules:"):].strip()
                if rest and rest != "[]":  # inline form not supported beyond []
                    pass
            continue
        if not stripped:
            continue
        if stripped.startswith("- "):
            if current is not None:
                entries.append(current)
            current = {}
            stripped = stripped[2:].strip()  # first key on same line as dash
        if current is None or ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        current[key.strip()] = _coerce(key.strip(), val)
    if current is not None:
        entries.append(current)
    return entries


def _serialize(entry: dict) -> list[str]:
    out = []
    first = True
    for key in FIELD_ORDER:
        if key not in entry:
            continue
        val = entry[key]
        if key == "cron":
            val = f'"{val}"'
        elif key == "enabled":
            val = "true" if val else "false"
        prefix = "  - " if first else "    "
        out.append(f"{prefix}{key}: {val}")
        first = False
    return out


def write_schedules(entries: list[dict], path: Path = SCHEDULE_PATH) -> None:
    header = _split_header(path.read_text(encoding="utf-8")) if path.exists() else ""
    body = ["schedules: []"] if not entries else ["schedules:"] + [
        ln for e in entries for ln in _serialize(e)
    ]
    text = (header + "\n\n" if header else "") + "\n".join(body) + "\n"
    path.write_text(text, encoding="utf-8")


# ---------- validation ----------

def validate(entry: dict) -> list[str]:
    errs = []
    if not entry.get("id"):
        errs.append("id 필수")
    if not entry.get("cron"):
        errs.append("cron 필수")
    if not entry.get("selector"):
        errs.append("selector 필수")
    if entry.get("mode") not in VALID_MODES:
        errs.append(f"mode 는 {VALID_MODES} 중 하나 (got {entry.get('mode')!r})")
    if not isinstance(entry.get("budget"), int):
        errs.append("budget 는 정수")
    if not isinstance(entry.get("enabled"), bool):
        errs.append("enabled 는 bool")
    cron = entry.get("cron", "")
    if cron and len(cron.split()) != 5:
        errs.append(f"cron 은 5-field (got {len(cron.split())} fields)")
    return errs


def _find(entries: list[dict], sid: str) -> int:
    for i, e in enumerate(entries):
        if e.get("id") == sid:
            return i
    return -1


# ---------- commands ----------

def cmd_list(args) -> int:
    entries = read_schedules()
    if not entries:
        print("(스케줄 없음)")
        return 0
    for e in entries:
        flag = "ON " if e.get("enabled") else "off"
        print(f"  [{flag}] {e.get('id'):<20} {e.get('cron'):<14} "
              f"→ {e.get('selector')} (mode={e.get('mode')}, budget=~{e.get('budget')})")
    return 0


def cmd_add(args) -> int:
    entries = read_schedules()
    if _find(entries, args.id) != -1:
        print(f"에러: id '{args.id}' 이미 존재 (update 사용)")
        return 1
    entry = {
        "id": args.id, "cron": args.cron, "selector": args.selector,
        "mode": args.mode, "budget": args.budget, "enabled": False,
    }
    errs = validate(entry)
    if errs:
        print("에러: " + "; ".join(errs))
        return 1
    entries.append(entry)
    write_schedules(entries)
    print(f"추가됨: {args.id} (enabled=false — 무인 발화는 enable + Owner 게이트)")
    return 0


def cmd_update(args) -> int:
    entries = read_schedules()
    idx = _find(entries, args.id)
    if idx == -1:
        print(f"에러: id '{args.id}' 없음")
        return 1
    for field in ("cron", "selector", "mode", "budget"):
        val = getattr(args, field)
        if val is not None:
            entries[idx][field] = val
    errs = validate(entries[idx])
    if errs:
        print("에러: " + "; ".join(errs))
        return 1
    write_schedules(entries)
    print(f"수정됨: {args.id}")
    return 0


def _set_enabled(sid: str, value: bool) -> int:
    entries = read_schedules()
    idx = _find(entries, sid)
    if idx == -1:
        print(f"에러: id '{sid}' 없음")
        return 1
    entries[idx]["enabled"] = value
    write_schedules(entries)
    state = "활성" if value else "비활성"
    note = " — 무인 cron 발화는 routine 등록(R3, Owner)도 필요" if value else ""
    print(f"{state}: {sid}{note}")
    return 0


def cmd_enable(args) -> int:
    return _set_enabled(args.id, True)


def cmd_disable(args) -> int:
    return _set_enabled(args.id, False)


def cmd_remove(args) -> int:
    entries = read_schedules()
    idx = _find(entries, args.id)
    if idx == -1:
        print(f"에러: id '{args.id}' 없음")
        return 1
    entries.pop(idx)
    write_schedules(entries)
    print(f"삭제됨: {args.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="스케줄 레지스트리 CRUD (SCHEDULE.yml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="스케줄 목록").set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="스케줄 추가")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--cron", required=True, help='5-field cron, 예: "0 9 * * 1-5"')
    p_add.add_argument("--selector", required=True, help="digest | maintenance | <tag> | TASK-NNN")
    p_add.add_argument("--mode", required=True, choices=VALID_MODES)
    p_add.add_argument("--budget", required=True, type=int)
    p_add.set_defaults(func=cmd_add)

    p_up = sub.add_parser("update", help="스케줄 수정")
    p_up.add_argument("--id", required=True)
    p_up.add_argument("--cron")
    p_up.add_argument("--selector")
    p_up.add_argument("--mode", choices=VALID_MODES)
    p_up.add_argument("--budget", type=int)
    p_up.set_defaults(func=cmd_update)

    for name, fn in (("enable", cmd_enable), ("disable", cmd_disable), ("remove", cmd_remove)):
        p = sub.add_parser(name, help=f"스케줄 {name}")
        p.add_argument("--id", required=True)
        p.set_defaults(func=fn)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
