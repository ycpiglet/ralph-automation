#!/usr/bin/env python3
"""COMPOUND 재발 metric — 반복 결함이 줄고 있는지 가시화.

TASK-149 (CYCLE-025). COMPOUND-017 (Andon) 사후 분석 자동화. DORA change-failure
패턴 차용 — compound_log 에서 재발 분포·critical·카테고리·미해결을 집계.

Usage:
  python scripts/compound_metrics.py
  python scripts/compound_metrics.py --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import kedb_search as kedb  # noqa: E402

ROOT = kedb.ROOT
COMPOUND_LOG = kedb.COMPOUND_LOG
CRITICAL_RECURRENCE = kedb.CRITICAL_RECURRENCE


def _is_closed(status: str) -> bool:
    return "적용 완료" in status


def compute_metrics(entries: list[dict]) -> dict:
    total = len(entries)
    by_category = Counter(e["category"] or "(none)" for e in entries)
    recurrence_hist = Counter(e["recurrence"] for e in entries)
    critical = [e["id"] for e in entries if e["recurrence"] >= CRITICAL_RECURRENCE]
    closed = sum(1 for e in entries if _is_closed(e["status"]))
    open_count = total - closed
    # 재발 1회 초과 = 실제 반복 발생한 결함
    repeated = [e["id"] for e in entries if e["recurrence"] > 1]
    return {
        "total": total,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))),
        "recurrence_histogram": {str(k): v for k, v in sorted(recurrence_hist.items())},
        "critical": critical,
        "critical_count": len(critical),
        "repeated": repeated,
        "repeated_count": len(repeated),
        "open": open_count,
        "closed": closed,
    }


def render_table(m: dict) -> str:
    out = ["COMPOUND 재발 metric (반복 결함 방지 현황)", "=" * 44]
    out.append(f"총 COMPOUND      : {m['total']}")
    out.append(f"미해결 (open)    : {m['open']}")
    out.append(f"해결 (closed)    : {m['closed']}")
    out.append(f"재발(>1) 결함    : {m['repeated_count']}  {', '.join(m['repeated']) or '-'}")
    out.append(f"critical(재발>={CRITICAL_RECURRENCE}): {m['critical_count']}  {', '.join(m['critical']) or '-'}")
    out.append("")
    out.append("카테고리 분포:")
    for cat, n in m["by_category"].items():
        out.append(f"  {cat:<22} {n}")
    out.append("")
    out.append("재발 횟수 분포:")
    for rec, n in m["recurrence_histogram"].items():
        out.append(f"  재발 {rec:>2}회: {n}")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="COMPOUND 재발 metric")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args(argv)

    if not COMPOUND_LOG.exists():
        print("compound_log.md not found.", file=sys.stderr)
        return 1
    entries = kedb.parse_compounds(COMPOUND_LOG.read_text(encoding="utf-8"))
    m = compute_metrics(entries)
    if args.format == "json":
        sys.stdout.write(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_table(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
