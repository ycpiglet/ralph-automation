#!/usr/bin/env python3
"""build_task_index — frontmatter → tasks.index.json (TASK-232, 구조화 TASK 레이어 ②).

전 TASK frontmatter 를 스키마(TASK-231) 준수 객체 배열 JSON 으로 파생한다 — UI·에이전트의
**단일 구조화 read 표면**(파싱 불요). 결정적(id 정렬)·재생성 가능·gitignore: canonical 은
frontmatter 라 인덱스를 커밋하지 않아 동시 재생성 충돌(COMPOUND-033)을 회피한다.
파서는 query_tasks.load_tasks() 재사용(신설 금지).

사용:
  python scripts/build_task_index.py            # tasks.index.json 생성/갱신
  python scripts/build_task_index.py --stdout   # 파일 안 쓰고 출력
  python scripts/build_task_index.py --check     # 인덱스 전 항목이 task.schema.json 통과하는지
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import query_tasks                 # load_tasks (단일 frontmatter 로더 재사용)
import validate_task_schema as vts  # 스키마 계약(TASK-231)

INDEX_PATH = ROOT / "tasks.index.json"
INDEX_VERSION = 1

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _id_num(tid: str) -> int:
    m = re.search(r"(\d+)", tid or "")
    return int(m.group(1)) if m else 0


def _title(path: Path) -> str:
    return re.sub(r"^TASK-\d+-", "", path.stem).replace("-", " ")


def build() -> list[dict]:
    """스키마 준수 task 객체 배열(결정적: id 오름차순). 파생 메타는 `_` 접두."""
    objs: list[dict] = []
    for path, fm in query_tasks.load_tasks():
        obj = dict(fm)
        obj["_path"] = str(path.relative_to(ROOT)).replace("\\", "/")
        obj["_title"] = _title(path)
        objs.append(obj)
    objs.sort(key=lambda o: _id_num(o.get("id", "")))
    return objs


def to_document(objs: list[dict] | None = None) -> dict:
    objs = build() if objs is None else objs
    return {"version": INDEX_VERSION, "generated_from": "TASK frontmatter (derived; canonical=*.md)",
            "count": len(objs), "tasks": objs}


def to_json(objs: list[dict] | None = None) -> str:
    return json.dumps(to_document(objs), ensure_ascii=False, indent=2) + "\n"


def write_index(path: Path = INDEX_PATH) -> Path:
    objs = build()
    path.write_text(to_json(objs), encoding="utf-8")
    return path


def check_against_schema() -> dict:
    """인덱스 각 task 가 task.schema.json 통과하는지 → {id: [errors]}."""
    schema = vts.load_schema()
    bad: dict[str, list[str]] = {}
    for obj in build():
        fm = {k: v for k, v in obj.items() if not k.startswith("_")}
        errs = vts.validate_frontmatter(fm, schema)
        if errs:
            bad[obj.get("id", "?")] = errs
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tasks.index.json 빌더 (TASK-232)")
    ap.add_argument("--stdout", action="store_true", help="파일 안 쓰고 출력")
    ap.add_argument("--check", action="store_true", help="인덱스가 task.schema.json 통과하는지 검증")
    args = ap.parse_args(argv)

    if args.check:
        bad = check_against_schema()
        if not bad:
            print("OK: 인덱스 전 항목이 task.schema.json 통과")
            return 0
        for tid, errs in bad.items():
            for e in errs:
                print(f"ERROR {tid}: {e}")
        return 1

    if args.stdout:
        print(to_json())
        return 0

    p = write_index()
    print(f"OK: {p.relative_to(ROOT)} ({to_document()['count']} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
