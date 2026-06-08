#!/usr/bin/env python3
"""secretary digest — Owner 개인 비서 데스크 요약 (TASK-226, MEETING-2026-06-04-001).

Owner 가 한 화면에서 "지금 무엇이 열려 있고 / 내 결정이 필요한 건 뭐고 / 무엇이 예정돼 있고 /
리스크는 뭔지" 를 보게 한다. 읽기 전용 소비자 — 제2 집계기(writer) 금지(단일출처, COMPOUND-032):
열린 작업은 backlog_sweep.collect() 를, 예정 스케줄은 schedule.read_schedules() 를 그대로 재사용.

secretary 는 Owner 보좌(R1만) — 보고·상기·종합만 하고 거버넌스 쓰기/우선순위 결정/감사/구현은
하지 않는다(agents/secretary/SKILL.md fence). CEO(회사 운영·결정)와 구조적으로 다른 계층.

사용:
  python scripts/secretary_digest.py            # DIGEST-{today}.md 생성 + 경로 출력
  python scripts/secretary_digest.py --stdout    # 파일 안 쓰고 본문만 출력
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backlog_sweep          # collect() — 열린 작업 단일 집계 재사용
import schedule as schedule_mod  # read_schedules() — 예정 스케줄 재사용
from generate_views import decision_lane  # BACKLOG.md decision lane과 동일 기준

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIGEST_DIR = ROOT / "agents" / "owner" / "digest"
_PRIO = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def _owner_decisions(open_tasks: list[dict]) -> list[dict]:
    """Owner 결정 대기 = BACKLOG decision lane 기준 REVIEW/ASK.

    DEFER는 "지금은 일부러 미룸"이라 Owner 데스크에서 결정 요구로 세지 않는다.
    """
    out = []
    for t in open_tasks:
        if decision_lane(t) in {"REVIEW", "ASK"}:
            out.append(t)
    return out


def render_digest(data: dict, schedules: list[dict], date: str) -> str:
    open_tasks = sorted(data.get("open_tasks", []),
                        key=lambda t: _PRIO.get(t.get("priority", ""), 9))
    decisions = _owner_decisions(open_tasks)
    enabled = [s for s in schedules if s.get("enabled")]

    lines = [
        f"# DIGEST {date} — Owner 데스크 (secretary)",
        "",
        "> secretary 가 backlog_sweep.collect() + SCHEDULE 를 종합한 읽기 전용 요약.",
        "> 결정·배정·감사·구현은 하지 않는다(보고·상기·제안만). canonical 출처는 BACKLOG.md.",
        "",
        f"Bottom Line: 열린 작업 {len(open_tasks)}건 · Owner 결정 대기 {len(decisions)}건 · "
        f"활성 스케줄 {len(enabled)}건 · 문서 위생 {data.get('doc_health', '?')}.",
        "",
        "## 내 결정이 필요한 것 (Owner gate)",
        "",
    ]
    if decisions:
        for t in decisions:
            note = t.get("gate") or "보류"
            lines.append(f"- **{t.get('id')}** [{t.get('priority')}] — {note}")
    else:
        lines.append("- (없음)")

    lines += ["", "## 열린 작업 (우선순위순)", ""]
    if open_tasks:
        for t in open_tasks:
            tags = ", ".join((t.get("tags") or [])[:3])
            lines.append(f"- {t.get('id')} [{t.get('status')}/{t.get('priority')}] "
                         f"· {t.get('owner')} · {tags}")
    else:
        lines.append("- (없음)")

    lines += ["", "## 예정 스케줄 (활성)", ""]
    if enabled:
        for s in enabled:
            lines.append(f"- {s.get('id')} — `{s.get('cron')}` → {s.get('selector')} "
                         f"(mode={s.get('mode')})")
    else:
        lines.append("- (활성 스케줄 없음 — schedule.py enable 로 활성화, 무인 발화는 Owner 게이트)")

    due = data.get("due_checks", {})
    lines += ["", "## 리스크 / 주기 신호", "",
              f"- 문서 위생: {data.get('doc_health', '?')}"]
    for line in due.values():
        lines.append(f"- {line}")

    lines.append("")
    return "\n".join(lines)


def build_digest() -> str:
    return render_digest(backlog_sweep.collect(), schedule_mod.read_schedules(),
                         time.strftime("%Y-%m-%d"))


def write_digest() -> Path:
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / f"DIGEST-{time.strftime('%Y-%m-%d')}.md"
    path.write_text(build_digest(), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="secretary digest — Owner 데스크 요약")
    ap.add_argument("--stdout", action="store_true", help="파일 안 쓰고 본문만 출력")
    args = ap.parse_args(argv)
    if args.stdout:
        print(build_digest())
    else:
        path = write_digest()
        print(f"DIGEST 작성: {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
