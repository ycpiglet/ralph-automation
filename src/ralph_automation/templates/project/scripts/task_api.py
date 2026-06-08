#!/usr/bin/env python3
"""task_api — 구조화 task read API (TASK-233, 구조화 TASK 레이어 ③ · read 부분).

tasks.index.json(빌더 재사용 — 항상 fresh) 위에서 get/query 조회. UI·에이전트가 파싱 없이
task 를 읽는 단일 표면. **write-through(frontmatter 안전 편집)와 MCP 래퍼는 본 TASK 잔여**
(sub-step) — read 가 먼저(저위험·고가치), write 는 frontmatter+body+INDEX 정합·단일 writer
설계가 필요하므로 별도.

사용:
  python scripts/task_api.py get TASK-231
  python scripts/task_api.py query --status 대기 --priority High
  python scripts/task_api.py query --tag task-model --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import re

import build_task_index            # build() 재사용 — 인덱스 빌더가 단일 read 소스
import task_events                 # 변경 이벤트 로그(④)
import validate_task_schema as vts  # 스키마 계약(①)
from check_agent_docs import parse_frontmatter  # 단일 frontmatter 파서 재사용

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"
INDEX_MD = TASKS_DIR / "INDEX.md"
VALID_STATUS = ["대기", "진행 중", "완료", "보류"]


def load_tasks() -> list[dict]:
    """현재 task 객체 배열(인덱스 빌더 재사용 — 디스크 인덱스 stale 와 무관하게 fresh)."""
    return build_task_index.build()


def get(task_id: str) -> dict | None:
    for obj in load_tasks():
        if obj.get("id") == task_id:
            return obj
    return None


def query(status: str | None = None, owner: str | None = None,
          priority: str | None = None, tag: str | None = None) -> list[dict]:
    """필드 AND 필터. None 인 조건은 무시. tags 는 포함(membership)."""
    out = []
    for obj in load_tasks():
        if status and obj.get("status") != status:
            continue
        if owner and obj.get("owner") != owner:
            continue
        if priority and obj.get("priority") != priority:
            continue
        if tag and tag not in (obj.get("tags") or []):
            continue
        out.append(obj)
    return out


# ---------- write-through (status) — TASK-233 ③ write 부분 ----------
# 한 TASK 의 status 를 3곳 정합 갱신(frontmatter + body `상태:` + INDEX 행) + 스키마 재검증 +
# (옵션) generate_views 재생성. 단일 writer 가정(데몬 아님). 무인 write 는 R3 — API 는 working
# tree 편집 → 기존 PR 흐름. status 가 가장 흔한 mutation 이라 우선 구현(일반 필드 write 는 잔여).

def _task_path(task_id: str) -> Path | None:
    if not re.match(r"^TASK-\d+$", task_id or ""):
        return None
    matches = sorted(TASKS_DIR.glob(f"{task_id}-*.md"))
    return matches[0] if matches else None


def _replace_frontmatter_field(text: str, field: str, value: str) -> str:
    """frontmatter 블록(첫 ---..---) 안의 `field:` 줄만 교체(body 불침범)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    head, rest = text[:end], text[end:]
    new_head = re.sub(rf"(?m)^{re.escape(field)}:.*$", f"{field}: {value}", head, count=1)
    return new_head + rest


def _replace_body_status(text: str, value: str) -> str:
    """body 의 첫 `상태:` 줄 교체(frontmatter 는 영문 status: 라 불침범)."""
    return re.sub(r"(?m)^상태:.*$", f"상태: {value}", text, count=1)


def _update_index_status(task_id: str, status: str, index_md: Path = INDEX_MD) -> bool:
    """INDEX.md 의 해당 TASK 행 2번째 셀(상태)을 교체. 성공 여부."""
    if not index_md.exists():
        return False
    txt = index_md.read_text(encoding="utf-8")
    pat = re.compile(rf"(\|\s*\[{re.escape(task_id)}\]\([^)]*\)\s*\|\s*)([^|]*?)(\s*\|)")
    new, n = pat.subn(lambda m: m.group(1) + status + m.group(3), txt, count=1)
    if n:
        index_md.write_text(new, encoding="utf-8")
    return bool(n)


def update_status(task_id: str, new_status: str, regenerate: bool = True,
                  emit_event: bool = True, actor: str = "task_api") -> dict:
    """status 를 3곳 정합 갱신 + 스키마 재검증(+옵션 재생성). {ok, errors, changed}.

    완료 전이는 별도 완료 ceremony(증거/리뷰/audit) 가 필요하니 본 API 는 워크플로 전이용이며
    완료 처리는 사람이 ceremony 와 함께 PR 로 마무리한다(가드).
    """
    if new_status not in VALID_STATUS:
        return {"ok": False, "errors": [f"status enum 위반: {new_status!r}"]}
    path = _task_path(task_id)
    if path is None:
        return {"ok": False, "errors": [f"TASK 파일 없음: {task_id}"]}
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        return {"ok": False, "errors": [f"{task_id}: frontmatter 없음"]}
    would = dict(fm)
    would["status"] = new_status
    errs = vts.validate_frontmatter(would, vts.load_schema())
    if errs:
        return {"ok": False, "errors": errs}

    old_status = fm.get("status")
    new_text = _replace_body_status(_replace_frontmatter_field(text, "status", new_status), new_status)
    path.write_text(new_text, encoding="utf-8")
    index_ok = _update_index_status(task_id, new_status)

    if emit_event:  # ④ 변경 이벤트(Linear SyncAction류) — best-effort, write 를 막지 않음
        try:
            task_events.append_event("update", task_id,
                                     {"status": {"from": old_status, "to": new_status}}, actor=actor)
        except Exception:
            pass

    changed = [str(path.relative_to(ROOT)).replace("\\", "/")]
    if index_ok:
        changed.append("agents/lead_engineer/tasks/INDEX.md")
    if regenerate:
        try:
            import generate_views
            generate_views.main()
            changed.append("generated views + tasks.index.json")
        except Exception as exc:
            return {"ok": True, "task_id": task_id, "status": new_status,
                    "changed": changed, "warn": f"재생성 생략: {exc}"}
    return {"ok": True, "task_id": task_id, "status": new_status, "changed": changed}


def _emit(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif data is None:
        print("(없음)")
    elif isinstance(data, list):
        for o in data:
            print(f"  {o.get('id')} [{o.get('status')}] {o.get('priority')} · {o.get('owner')} · {o.get('_title')}")
        print(f"  — {len(data)}건")
    else:
        print(f"{data.get('id')} [{data.get('status')}] {data.get('priority')} · {data.get('owner')}")
        print(f"  {data.get('_title')} · tags={data.get('tags')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="구조화 task read API (TASK-233, read)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="ID 로 단일 조회")
    g.add_argument("id")
    g.add_argument("--json", action="store_true")

    q = sub.add_parser("query", help="필드 필터 조회")
    q.add_argument("--status")
    q.add_argument("--owner")
    q.add_argument("--priority")
    q.add_argument("--tag")
    q.add_argument("--json", action="store_true")

    s = sub.add_parser("set-status", help="status 3곳 정합 갱신(write-through)")
    s.add_argument("id")
    s.add_argument("status", choices=VALID_STATUS)
    s.add_argument("--no-regen", action="store_true", help="generate_views 재생성 생략")
    s.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "get":
        _emit(get(args.id), args.json)
        return 0
    if args.cmd == "query":
        _emit(query(status=args.status, owner=args.owner, priority=args.priority, tag=args.tag), args.json)
        return 0
    if args.cmd == "set-status":
        res = update_status(args.id, args.status, regenerate=not args.no_regen)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif res["ok"]:
            print(f"OK: {res['task_id']} → {res['status']} (변경: {', '.join(res['changed'])})")
        else:
            print("실패: " + "; ".join(res["errors"]))
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
