#!/usr/bin/env python3
"""Beta Tester 탐색 라운드 필요 여부 advisory (read-only, source of truth 아님).

scribe_due.py 와 같은 패턴: SKILL.md 의 "매 릴리즈/사이클 종료 전 1라운드" 트리거가
적혀만 있고 자동 발화 장치가 없어 휴면(BTC 산출 0건)이던 문제를, 정량 cadence 신호로
자동 평가한다. 설계 근거: agents/beta_tester/SKILL.md §행동 지침,
MEETING-2026-06-01-001 (AUDIT-2026-06-01-008 — 휴면 역할 활성화).

신호: 최신 CYCLE 번호 vs 가장 최근 베타 라운드가 다룬 CYCLE 번호의 격차.
  - 베타 라운드 근거 = agents/beta_tester/test_cases/ROUNDS.md(클린 라운드 포함, source of
    truth) + BTC-*.md 의 명시적 `라운드: CYCLE-NNN` 마커. 둘 다 없으면 0건으로 간주.
    (BTC 상태열의 과거 'CYCLE-006 수정 완료' 같은 임의 참조는 세지 않는다.)

임계 (cycles_behind = 최신 CYCLE - 최근 베타 CYCLE):
  0       ok        현재 사이클 베타 라운드 기록됨
  1       due       다음 사이클 종료/릴리즈 전 1라운드 권장
  >= 2    overdue   베타 게이트 누락 누적 — 라운드 필수(스킵 시 다음 RETRO §1 에 추적)

사용:
  python scripts/beta_tester_due.py            # 사람용 출력
  python scripts/beta_tester_due.py --quiet    # 상태 한 줄 (advisory라 항상 종료코드 0)
"""
import re
import sys
from pathlib import Path

try:  # Windows 콘솔(cp949)에서도 UTF-8 출력
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LEAD = ROOT / "agents" / "lead_engineer"
BTC_DIR = ROOT / "agents" / "beta_tester" / "test_cases"
ROUNDS = BTC_DIR / "ROUNDS.md"  # 클린 라운드(BTC 0건)도 기록 — 라운드별 `CYCLE-NNN` 한 줄

DUE_AT = 1
OVERDUE_AT = 2


def newest_cycle() -> int:
    """최신 사이클 = max(CYCLE-*.md, reviews/REVIEW-*.md) 번호. 경량 사이클은
    REVIEW 만으로 기록될 수 있어(CYCLE 파일 044 vs REVIEW 061 drift) REVIEW 도 본다."""
    nums = []
    for base, pat in ((LEAD, "CYCLE-*.md"), (LEAD / "reviews", "REVIEW-*.md")):
        for p in base.glob(pat):
            m = re.search(r"-(\d+)", p.stem)
            if m:
                nums.append(int(m.group(1)))
    return max(nums) if nums else -1


def latest_beta_cycle() -> int:
    """마지막 베타 라운드가 다룬 최대 CYCLE 번호(없으면 -1).

    ROUNDS.md(클린 라운드 포함, source of truth)를 우선 스캔하고, BTC 케이스가
    명시적으로 `라운드: CYCLE-NNN` 마커를 단 경우도 합산한다. 임의 CYCLE 언급은
    세지 않는다(BTC 상태열의 'CYCLE-006 수정 완료' 같은 과거 참조 오검출 방지)."""
    best = -1
    if ROUNDS.exists():
        for m in re.finditer(r"CYCLE-(\d+)", ROUNDS.read_text(encoding="utf-8", errors="ignore")):
            best = max(best, int(m.group(1)))
    if BTC_DIR.exists():
        for p in BTC_DIR.glob("BTC-*.md"):
            for m in re.finditer(r"(?:라운드|round)\s*:?\s*CYCLE-(\d+)", p.read_text(encoding="utf-8", errors="ignore")):
                best = max(best, int(m.group(1)))
    return best


def classify(n: int) -> str:
    if n >= OVERDUE_AT:
        return "overdue"
    if n >= DUE_AT:
        return "due"
    return "ok"


def main() -> int:
    quiet = "--quiet" in sys.argv
    cyc = newest_cycle()
    beta = latest_beta_cycle()
    if cyc < 0:
        print("[beta_tester_due] ok — CYCLE 기록 없음")
        return 0
    behind = cyc - beta if beta >= 0 else cyc + 1  # 베타 기록 전무 시 최대 격차로 취급
    state = classify(behind)

    base = (f"최신 CYCLE-{cyc:03d}, 최근 베타 라운드 "
            + (f"CYCLE-{beta:03d}" if beta >= 0 else "기록 없음"))
    print(f"[beta_tester_due] {state} — {base} (behind {behind})")
    if not quiet:
        if state != "ok":
            print("  → Beta 탐색 라운드 절차: agents/beta_tester/SKILL.md "
                  "§탐색 시나리오 / 테스트 케이스 기록 형식 → BTC-*.md")
            print("  → 발견 케이스는 QA 가 BUG 리포트로 변환(CLAUDE.md §4 Beta→QA 흐름).")
        else:
            print("  → 라운드 불요. cadence backstop: 매 사이클 종료/릴리즈 전 1회 평가.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
