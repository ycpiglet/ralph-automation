#!/usr/bin/env python3
"""작업 목록 단일 sweep — 모든 추적 surface 를 한 화면에 집계 (read-only advisory).

목적: "작업 목록 갱신/다음 할 일" 요청 시 *단일 출처*(예: STATUS 큐)만 읽어
항목을 누락하는 것을 막는다. 백로그는 여러 surface 에 흩어져 있고(아래),
하나만 보면 거기에 없는 항목(메모리의 Owner 게이트·due-check·사이클 이월)이 통째로 빠진다.
근거: COMPOUND-032 (backlog-source-fragmentation, 재발 2회 → 기계 포레싱 함수).

기계 판독으로 집계(이 스크립트가 직접):
  1. 열린 TASK      — query_tasks 대기 + 진행 중
  2. 주기/휴면 역할 due — scribe_due / beta_tester_due / doc_steward_due
  3. 문서 위생 advisory — doc_health_report 요약

수동 재확인이 필요한 산문/외부 surface(이 스크립트가 포인터만 출력):
  4. 최신 PLAN report — reports/PLAN-*.md §다음/권고
  5. 최신 REVIEW      — reviews/REVIEW-*.md §다음/이월
  6. 최근 CYCLE 이월  — 최근 N개 CYCLE-*.md §다음/이월
  7. 메모리          — MEMORY.md 의 열린 Owner 게이트(claude 라이브·토큰 ledger 등)

이 스크립트는 source of truth 가 아니다. 출력은 "다음 할 일을 STATUS 큐 + INDEX 대기
TASK 로 등록"하기 위한 reconcile 입력이다(COMPOUND-031/032).

사용:
  python scripts/backlog_sweep.py
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # Windows 콘솔(cp949)에서도 UTF-8 출력
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LEAD = ROOT / "agents" / "lead_engineer"
RECENT_CYCLES = 3


def _run(args: list[str]) -> str:
    """스크립트를 subprocess 로 실행하고 stdout 을 돌려준다(실패는 한 줄 문자열로 흡수)."""
    try:
        out = subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 자식이 cp949 로 흘려도 죽지 않게(안전망)
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # 자식 stdout 을 UTF-8 로 강제
            timeout=60,
        )
        return (out.stdout or out.stderr or "").strip()
    except Exception as exc:  # never-raise: sweep 은 advisory
        return f"(실행 실패: {exc})"


def open_tasks() -> list[dict]:
    tasks: list[dict] = []
    for status in ("대기", "진행 중", "보류"):
        raw = _run(["scripts/query_tasks.py", "--status", status, "--format", "json"])
        try:
            tasks.extend(json.loads(raw))
        except Exception:
            pass
    # 우선순위 내림차순
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    tasks.sort(key=lambda t: order.get(t.get("priority", ""), 9))
    return tasks


def due_line(script: str) -> str:
    line = _run([f"scripts/{script}", "--quiet"]).splitlines()
    return line[0] if line else f"[{script}] (출력 없음)"


def doc_health_summary() -> str:
    raw = _run(["scripts/doc_health_report.py", "--json"])
    try:
        data = json.loads(raw)
        findings = data.get("findings", data if isinstance(data, list) else [])
        sev = {}
        for f in findings:
            s = f.get("severity", f.get("level", "?"))
            sev[s] = sev.get(s, 0) + 1
        if not sev:
            return "advisory 0건"
        return ", ".join(f"{k} {v}" for k, v in sorted(sev.items()))
    except Exception:
        # JSON 파싱 실패 시 사람용 마지막 줄
        return raw.splitlines()[-1] if raw else "(요약 불가)"


def newest(glob_dir: Path, pattern: str, n: int = 1) -> list[Path]:
    files = sorted(
        glob_dir.glob(pattern),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem)] or [0],
    )
    return files[-n:][::-1]


def collect() -> dict:
    """모든 추적 surface 를 구조화 dict 로 — 작업목록 API 의 단일 데이터 출처.

    print_human / print_json 둘 다 이걸 렌더한다. 외부(MCP·스케줄러·HTTP)도 이 함수를
    호출해 같은 데이터를 받는다(AUDIT-2026-06-04-004).
    """
    return {
        "pointer": "agents/lead_engineer/tasks/BACKLOG.md",
        "open_tasks": open_tasks(),
        "due_checks": {
            s[:-3].replace("_due", ""): due_line(s)
            for s in ("scribe_due.py", "beta_tester_due.py", "doc_steward_due.py")
        },
        "doc_health": doc_health_summary(),
        "prose_pointers": {
            "latest_plan": [str(p.relative_to(ROOT)) for p in newest(LEAD / "reports", "PLAN-*.md")],
            "latest_review": [str(p.relative_to(ROOT)) for p in newest(LEAD / "reviews", "REVIEW-*.md")],
            "recent_cycles": [p.stem for p in newest(LEAD, "CYCLE-*.md", RECENT_CYCLES)],
            "memory_note": "Owner 게이트는 보류 TASK 로 이주됨(claude 라이브=TASK-221·토큰 ledger=TASK-222).",
        },
    }


def print_human(data: dict) -> None:
    print("=" * 64)
    print("작업 목록 SWEEP (모든 추적 surface — 단일 출처 금지)")
    print("=" * 64)
    print(f"단일 포인터: {data['pointer']}  ← git pull 후 먼저 읽기")
    print("(BACKLOG.md = TASK frontmatter 생성형 canonical. 아래는 + 런타임 신호 sweep.)")

    print("\n[1] 열린 TASK (query_tasks 대기 + 진행 중 + 보류, 우선순위순)")
    if not data["open_tasks"]:
        print("  (없음 — query_tasks 조회 실패 시 수동 확인)")
    for t in data["open_tasks"]:
        print(f"  - {t.get('id')} [{t.get('status')}] {t.get('priority')} "
              f"· {t.get('owner')} · {', '.join(t.get('tags', [])[:3])}")

    print("\n[2] 주기/휴면 역할 due (advisory)")
    for line in data["due_checks"].values():
        print(f"  {line}")

    print("\n[3] 문서 위생 (doc_health_report advisory)")
    print(f"  {data['doc_health']}")

    pp = data["prose_pointers"]
    print("\n[4-7] 수동 재확인 — 산문/외부 surface (포인터만; 직접 읽어 reconcile)")
    for p in pp["latest_plan"]:
        print(f"  [4] 최신 PLAN  → {p}  (§다음/권고)")
    for p in pp["latest_review"]:
        print(f"  [5] 최신 REVIEW → {p}  (§다음/이월)")
    print(f"  [6] 최근 CYCLE 이월 → {', '.join(pp['recent_cycles'])}  (각 §다음/이월)")
    print(f"  [7] 메모리(MEMORY.md) → {pp['memory_note']} 메모리는 'why' 노트.")
    print("      ※ 새 메모리-only 게이트가 생기면 반드시 보류 TASK 로 미러(repo 공유 — COMPOUND-032).")

    print("\n→ canonical 은 BACKLOG.md(생성형, 위 [1] 과 동일 출처). 새 항목은 산문/메모리가 아니라 "
          "TASK 로 등록해야 BACKLOG.md 에 반영된다(COMPOUND-031/032). 단일 surface 만 보고 목록 만들지 말 것.")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="모든 추적 surface 를 한 화면에 집계(작업목록 API)")
    ap.add_argument("--json", action="store_true", help="구조화 JSON 출력(MCP·스케줄러·HTTP 가 소비)")
    args = ap.parse_args(argv)

    data = collect()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_human(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
