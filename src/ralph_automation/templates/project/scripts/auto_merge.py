#!/usr/bin/env python3
"""자율 머지 게이트 (Autonomous Merge Gate) — 승인 최소화 머지 로직.

설계 근거: AGENTS.md §3.5.2, EVIDENCE-2026-06-01-003, MEETING-2026-06-01-003.
가역성×blast-radius(§3.5.1) 적용: main 머지는 Vercel prod deploy 를 유발하나 instant
rollback + revert PR 로 되돌릴 수 있어 **R2(act+flag)** 이지 R3 가 아니다. 따라서 아래
게이트가 모두 통과하면 사람 승인 없이 머지하고, 비가역 surface 만 R3(escalate)로 보낸다.

gh CLI 로 PR 상태를 기계 판정한다(읽기 전용 기본, --execute 시에만 머지).

게이트(모두 true → AUTO-MERGE, R2):
  1. state=OPEN, mergeStateStatus=CLEAN, mergeable=MERGEABLE
  2. 모든 status check = SUCCESS (pending/failing/required-skip 없음)
  3. reviewDecision 이 CHANGES_REQUESTED 아님
  4. 변경 파일 중 R3 surface(아래) 없음
  5. 코드(비문서) 변경량 <= CODE_LINE_CAP (문서/기록만이면 면제)

ESCALATE(하나라도 → 사람 결정, R3): 위 위반, CI red, R3 surface 포함.

사용:
  python scripts/auto_merge.py <PR>            # 판정만(dry-run)
  python scripts/auto_merge.py <PR> --execute  # 통과 시 squash 머지+브랜치 삭제
"""
import json
import re
import subprocess
import sys

try:  # Windows 콘솔(cp949)에서도 UTF-8 출력
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_LINE_CAP = 600  # 비문서 변경 라인(additions+deletions) 소프트 상한

# 비가역/고-blast surface — 하나라도 변경되면 R3(사람 결정).
R3_PATTERNS = [
    re.compile(r"^\.github/workflows/"),     # CI 변경은 모든 미래 실행에 영향(비가역-기본)
    re.compile(r"^Managed database/"),               # row-level policy/마이그레이션
    re.compile(r"migrate\.py$"),
    re.compile(r"(^|/)\.env"),               # 환경/시크릿
    re.compile(r"secret(?!ary|ariat)", re.IGNORECASE),  # "secret(s/_key/.json)" O, "secretary" 오탐 X
    re.compile(r"^vercel\.(json|ts)$"),      # 배포/런타임 설정
]

DOC_PATTERNS = [
    re.compile(r"\.md$"),
    re.compile(r"^agents/"),                 # 운영 기록(TASK/MEETING/AUDIT/SKILL/notes…)
]


def gh_json(pr: str, fields: str) -> dict:
    out = subprocess.run(
        ["gh", "pr", "view", pr, "--json", fields],
        capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode != 0:
        raise SystemExit(f"gh 실패: {out.stderr.strip()}")
    return json.loads(out.stdout)


def is_doc(path: str) -> bool:
    return any(p.search(path) for p in DOC_PATTERNS)


def r3_hits(files: list) -> list:
    hits = []
    for f in files:
        path = f.get("path", "")
        for pat in R3_PATTERNS:
            if pat.search(path):
                hits.append(path)
                break
    return sorted(set(hits))


def evaluate(pr: str) -> tuple:
    d = gh_json(pr, "state,mergeStateStatus,mergeable,reviewDecision,statusCheckRollup,files,title")
    reasons_block = []  # R3/escalate 사유
    if d.get("state") != "OPEN":
        return "SKIP", [f"PR state={d.get('state')} (이미 닫힘/머지)"], d

    checks = d.get("statusCheckRollup") or []
    bad = []
    for c in checks:
        concl = c.get("conclusion") or c.get("state") or ""
        name = c.get("name") or c.get("context") or "?"
        if concl in ("SUCCESS", "SKIPPED", "NEUTRAL"):
            continue
        bad.append(f"{name}={concl or 'pending'}")
    if bad:
        reasons_block.append(f"CI 미통과/대기: {', '.join(bad)}")

    if d.get("mergeStateStatus") != "CLEAN":
        reasons_block.append(f"mergeStateStatus={d.get('mergeStateStatus')} (CLEAN 아님)")
    if d.get("mergeable") != "MERGEABLE":
        reasons_block.append(f"mergeable={d.get('mergeable')}")
    if d.get("reviewDecision") == "CHANGES_REQUESTED":
        reasons_block.append("reviewDecision=CHANGES_REQUESTED")

    files = d.get("files") or []
    hits = r3_hits(files)
    if hits:
        reasons_block.append(f"R3 surface 변경(사람 결정 필요): {hits}")

    code_lines = sum((f.get("additions", 0) + f.get("deletions", 0))
                     for f in files if not is_doc(f.get("path", "")))
    if code_lines > CODE_LINE_CAP:
        reasons_block.append(f"비문서 변경 {code_lines}줄 > cap {CODE_LINE_CAP} (대형 diff — 검토)")

    if reasons_block:
        return "ESCALATE", reasons_block, d
    return "AUTO-MERGE", [f"코드 {code_lines}줄, 파일 {len(files)}개, 전 check green, CLEAN"], d


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    pr = args[0]
    execute = "--execute" in sys.argv
    verdict, reasons, d = evaluate(pr)
    print(f"[auto_merge] PR #{pr} \"{d.get('title','')[:50]}\" → {verdict}")
    for r in reasons:
        print(f"  - {r}")
    if verdict == "AUTO-MERGE":
        if execute:
            m = subprocess.run(
                ["gh", "pr", "merge", pr, "--squash", "--delete-branch"],
                capture_output=True, text=True, encoding="utf-8",
            )
            # gh 는 원격 머지 후 로컬 정리에서 SSH 실패할 수 있으나 머지 자체는 API.
            print(m.stdout.strip() or m.stderr.strip())
            print("  → 머지 실행됨(--execute). 로컬 main 동기화 필요: git fetch && git reset --hard origin/main")
        else:
            print("  → 게이트 통과. 머지하려면 --execute (R2: 머지 후 BRIEF 에 deploy+rollback 1줄).")
        return 0
    if verdict == "ESCALATE":
        print("  → R3: Owner 결정 필요. 자동 머지 안 함.")
        return 1
    return 0  # SKIP


if __name__ == "__main__":
    raise SystemExit(main())
