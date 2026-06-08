#!/usr/bin/env python3
"""KEDB(Known Error Database) 검색 — 작업 시작 시 관련 COMPOUND 자동 surface.

TASK-150 (CYCLE-025). §17.1 strict gate 의 자동화 — "LLM 자발 검색에 의존 안 함".
작업 키워드(도구 / 파일 경로 / 패턴)로 `compound_log.md` 를 검색해, 같은 결함을
*시작 전에* 알려준다.

Usage:
  python scripts/kedb_search.py save_report VIEW
  python scripts/kedb_search.py 부산물 commit --critical
  python scripts/kedb_search.py --category process-omission
  python scripts/kedb_search.py VIEW --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
COMPOUND_LOG = ROOT / "agents" / "lead_engineer" / "compound_log.md"
CRITICAL_RECURRENCE = 3


def _field(entry: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:\s*(.+?)\s*$", entry, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _subsection(entry: str, header: str) -> str:
    m = re.search(rf"####\s+{re.escape(header)}(.*?)(?=^####\s|\Z)", entry, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_compounds(text: str) -> list[dict]:
    """compound_log.md 를 COMPOUND 항목 리스트로 파싱 (v1/v2 혼재 graceful)."""
    entries: list[dict] = []
    for chunk in re.split(r"(?=^### COMPOUND-\d+\s*$)", text, flags=re.MULTILINE):
        head = chunk.strip().split("\n", 1)[0] if chunk.strip() else ""
        m = re.match(r"### COMPOUND-(\d+)\s*$", head)
        if not m:
            continue
        number = int(m.group(1))
        rec_raw = _field(chunk, "재발 횟수")
        try:
            recurrence = int(rec_raw)
        except ValueError:
            recurrence = 0
        pattern = _subsection(chunk, "발견한 패턴") or _field(chunk, "발견한 패턴")
        entries.append({
            "id": f"COMPOUND-{number:03d}",
            "number": number,
            "category": _field(chunk, "카테고리"),
            "recurrence": recurrence,
            "status": _subsection(chunk, "상태") or _field(chunk, "상태"),
            "pattern": " ".join(pattern.split()),
            "text": chunk,
        })
    return entries


def search(entries: list[dict], keywords: list[str], category: str | None,
           critical_only: bool) -> list[dict]:
    results: list[dict] = []
    for e in entries:
        if category and e["category"] != category:
            continue
        if critical_only and e["recurrence"] < CRITICAL_RECURRENCE:
            continue
        if keywords:
            haystack = e["text"].lower()
            score = sum(haystack.count(k.lower()) for k in keywords)
            if score == 0:
                continue
        else:
            score = 0
        results.append({**e, "score": score})
    # 점수 desc, 동점은 재발 desc, 그다음 번호 desc (최신 우선)
    results.sort(key=lambda e: (-e["score"], -e["recurrence"], -e["number"]))
    return results


def render_table(rows: list[dict], keywords: list[str]) -> str:
    if not rows:
        kw = " ".join(keywords) if keywords else "(filter only)"
        return f"KEDB: no matching COMPOUND for '{kw}'. (신규 영역일 수 있음 — §17.1)\n"
    out = []
    out.append(f"{'ID':<14} {'CAT':<20} {'REC':>3} {'HIT':>3}  PATTERN")
    out.append("-" * 78)
    for e in rows:
        flag = " !" if e["recurrence"] >= CRITICAL_RECURRENCE else ""
        pat = e["pattern"][:60] + ("…" if len(e["pattern"]) > 60 else "")
        out.append(f"{e['id']:<14} {e['category'][:20]:<20} {e['recurrence']:>3}{flag:<2} {e['score']:>3}  {pat}")
    out.append("")
    out.append(f"{len(rows)} COMPOUND(s) matched. (! = critical, 재발>={CRITICAL_RECURRENCE})")
    return "\n".join(out) + "\n"


def render_json(rows: list[dict]) -> str:
    payload = [{k: e[k] for k in ("id", "category", "recurrence", "score", "pattern", "status")} for e in rows]
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KEDB(Known Error Database) 검색 — 작업 시작 시 관련 COMPOUND surface")
    parser.add_argument("keywords", nargs="*", help="검색 키워드 (도구/파일/패턴). 본문에 매칭")
    parser.add_argument("--category", help="카테고리 정확 필터 (예: process-omission)")
    parser.add_argument("--critical", action="store_true", help=f"재발>={CRITICAL_RECURRENCE} 만")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args(argv)

    if not COMPOUND_LOG.exists():
        print("KEDB: compound_log.md not found.", file=sys.stderr)
        return 1
    entries = parse_compounds(COMPOUND_LOG.read_text(encoding="utf-8"))
    rows = search(entries, args.keywords, args.category, args.critical)
    if args.format == "json":
        sys.stdout.write(render_json(rows))
    else:
        sys.stdout.write(render_table(rows, args.keywords))
    return 0


if __name__ == "__main__":
    sys.exit(main())
