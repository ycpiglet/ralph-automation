#!/usr/bin/env python3
"""validate_task_schema — TASK frontmatter를 schemas/task.schema.json 계약으로 검증 (TASK-231).

구조화 TASK 레이어 ① 단계. frontmatter(parse_frontmatter, 값은 문자열)를 JSON Schema
**subset**(required·const·type·enum·pattern)으로 검증한다. jsonschema 의존 회피(repo의
PyYAML 비의존 정책과 일관 — CI clean env). schema 파일은 표준 JSON Schema라 외부 도구/UI도
사용 가능. 숫자(est_hours/est_tokens)는 frontmatter 텍스트라 문자열에서 coerce 검증한다.

사용:
  python scripts/validate_task_schema.py          # 전 TASK(frontmatter 보유) 검증
  python scripts/validate_task_schema.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_agent_docs import parse_frontmatter  # 단일 frontmatter 파서 재사용(신설 금지)

SCHEMA_PATH = ROOT / "schemas" / "task.schema.json"
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"
FRONTMATTER_REQUIRED_FROM = 48  # TASK-048+ 만 frontmatter 계약 적용(check_agent_docs 와 동일)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_int(v) -> bool:
    return isinstance(v, int) or (isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()) is not None)


def _is_num(v) -> bool:
    if isinstance(v, (int, float)):
        return True
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def validate_frontmatter(fm: dict, schema: dict) -> list[str]:
    """JSON Schema subset 검증 → 에러 문자열 리스트(빈 리스트=통과).

    frontmatter 값은 parse_frontmatter 산출이라 스칼라가 문자열·리스트가 list.
    integer/number 는 문자열 표현을 coerce 해서 검사한다.
    """
    errors: list[str] = []
    props = schema.get("properties", {})

    for key in schema.get("required", []):
        v = fm.get(key)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            errors.append(f"required 누락: {key}")

    for key, spec in props.items():
        if key not in fm:
            continue
        v = fm[key]
        t = spec.get("type")
        if t == "integer" and not _is_int(v):
            errors.append(f"{key}: integer 아님 ({v!r})")
        elif t == "number" and not _is_num(v):
            errors.append(f"{key}: number 아님 ({v!r})")
        elif t == "array" and not isinstance(v, list):
            errors.append(f"{key}: array 아님 ({v!r})")
        elif t == "string" and not isinstance(v, str):
            errors.append(f"{key}: string 아님 ({v!r})")
        if "const" in spec and v != spec["const"]:
            errors.append(f"{key}: const {spec['const']!r} 아님 ({v!r})")
        if "enum" in spec and v not in spec["enum"]:
            errors.append(f"{key}: enum {spec['enum']} 아님 ({v!r})")
        if "pattern" in spec and isinstance(v, str) and not re.search(spec["pattern"], v):
            errors.append(f"{key}: pattern {spec['pattern']} 불일치 ({v!r})")
    return errors


def iter_task_frontmatters():
    """(path, frontmatter|None, preerrors|None) — TASK-048+ 만."""
    for path in sorted(TASKS_DIR.glob("TASK-*.md")):
        m = re.match(r"TASK-(\d+)", path.name)
        if not m or int(m.group(1)) < FRONTMATTER_REQUIRED_FROM:
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm is None:
            yield path, None, ["frontmatter 없음(TASK-048+ 필수)"]
        else:
            yield path, fm, None


def validate_all(schema: dict | None = None) -> dict:
    """{task_filename: [errors]} — 위반 TASK만."""
    schema = schema or load_schema()
    results: dict[str, list[str]] = {}
    for path, fm, pre in iter_task_frontmatters():
        errs = pre if pre is not None else validate_frontmatter(fm, schema)
        if errs:
            results[path.name] = errs
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TASK frontmatter 스키마 검증 (TASK-231)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = validate_all()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif not res:
        print("OK: 전 TASK frontmatter 가 task.schema.json 통과")
    else:
        for name, errs in res.items():
            for e in errs:
                print(f"ERROR {name}: {e}")
        print(f"FAILED: {sum(len(e) for e in res.values())} error(s) in {len(res)} task(s)")
    return 1 if res else 0


if __name__ == "__main__":
    raise SystemExit(main())
