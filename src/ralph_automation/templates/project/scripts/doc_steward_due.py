#!/usr/bin/env python3
"""Doc Steward 문서-정합성 점검 필요 여부 advisory (read-only, source of truth 아님).

scribe_due.py 와 같은 패턴: 적힌 트리거(SKILL.md §Invocation Triggers)를 사람이
'알아차려야' 발화하던 문제를, 정량 신호 + cadence backstop 으로 자동 평가한다.
설계 근거: agents/doc_steward/SKILL.md §Invocation Triggers, MEETING-2026-06-01-001
(AUDIT-2026-06-01-008 — 휴면 역할 활성화).

check_agent_docs.py 가 이미 잡는 것(frontmatter/INDEX 정합성/ISO 8601)은 중복하지
않는다. Doc Steward 고유 영역인 **drift(정합성 표류)** 신호만 본다:

  D1 org-chart drift : agents/<role>/SKILL.md 가 있으나 CLAUDE.md 조직도에서
                       참조되지 않는 고아 역할 문서 (예: 통합/폐기됐는데 파일만 잔존).
  D2 missing review  : 최신 CYCLE-NNN.md 에 대응하는 REVIEW-NNN.md 가 없음.

임계 (drift 신호 합):
  0       ok        점검 불요
  1-2     due       다음 Review/거버넌스에서 Doc Steward Check 권장
  >= 3    overdue   Doc Steward Check 필수(스킵 시 다음 RETRO §1 에 추적)

사용:
  python scripts/doc_steward_due.py            # 사람용 출력
  python scripts/doc_steward_due.py --quiet    # 상태 한 줄 (advisory라 항상 종료코드 0)
"""
import re
import sys
from pathlib import Path

try:  # Windows 콘솔(cp949)에서도 UTF-8 출력
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
CLAUDE_MD = ROOT / "CLAUDE.md"
LEAD = AGENTS / "lead_engineer"
REVIEWS = LEAD / "reviews"

DUE_AT = 1
OVERDUE_AT = 3

# CLAUDE.md 조직도에 의도적으로 없을 수 있는(=고아 아님) 디렉토리.
NON_ORGCHART_OK = {"lead_engineer"}  # lead 는 표 밖 facilitator 로 상시 등장하므로 별도 처리 불필요


def skill_dirs() -> set:
    return {p.parent.name for p in AGENTS.glob("*/SKILL.md")}


def orgchart_dirs() -> set:
    """CLAUDE.md 본문에서 참조되는 agents/<dir>/ 디렉토리 집합."""
    if not CLAUDE_MD.exists():
        return set()
    text = CLAUDE_MD.read_text(encoding="utf-8")
    return set(re.findall(r"agents/([a-z_]+)/", text))


def orphan_role_docs() -> list:
    """SKILL.md 는 있는데 조직도가 참조하지 않는 역할 디렉토리."""
    referenced = orgchart_dirs() | NON_ORGCHART_OK
    return sorted(d for d in skill_dirs() if d not in referenced)


def newest_num(glob: str, base: Path) -> int:
    nums = []
    for p in base.glob(glob):
        m = re.search(r"-(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else -1


def missing_review() -> int:
    """최신 사이클 번호에 대응하는 REVIEW 가 없으면 그 번호를 반환(없으면 -1).

    최신 사이클 = max(CYCLE-*.md, REVIEW-*.md) 번호. 본 프로젝트는 경량 사이클을
    REVIEW-NNN(canonical)만으로 기록하고 CYCLE-NNN.md 파일은 생략할 수 있어(045~),
    REVIEW 를 사이클 마커로 인정한다 — CYCLE 파일 부재 자체는 drift 가 아니다."""
    cyc = max(newest_num("CYCLE-*.md", LEAD), newest_num("REVIEW-*.md", REVIEWS))
    if cyc < 0:
        return -1
    rev_path = REVIEWS / f"REVIEW-{cyc:03d}.md"
    return cyc if not rev_path.exists() else -1


def classify(n: int) -> str:
    if n >= OVERDUE_AT:
        return "overdue"
    if n >= DUE_AT:
        return "due"
    return "ok"


def main() -> int:
    quiet = "--quiet" in sys.argv
    orphans = orphan_role_docs()
    miss = missing_review()
    drift = len(orphans) + (1 if miss >= 0 else 0)
    state = classify(drift)

    print(f"[doc_steward_due] {state} — drift 신호 {drift}개")
    if not quiet:
        if orphans:
            print(f"  D1 org-chart drift: 조직도 미참조 역할 문서 {orphans} "
                  f"→ CLAUDE.md 조직도에 재등재 또는 폐기/병합 결정 필요")
        if miss >= 0:
            print(f"  D2 missing review: CYCLE-{miss:03d} 에 대응하는 "
                  f"REVIEW-{miss:03d}.md 없음")
        if state != "ok":
            print("  → Doc Steward Check 절차: agents/doc_steward/SKILL.md "
                  "§Invocation Triggers / Output Contract")
        else:
            print("  → 점검 불요. cadence backstop: 매 Review/RETRO 1회 평가(이미 워크플로에 존재).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
