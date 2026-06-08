#!/usr/bin/env python3
"""pre-commit 게이트 — 알려진 결함 패턴을 commit 시점에 차단.

TASK-147 (CYCLE-025). 재발을 git stage 단계에서 막는다:
  1. tmp_*/ 부산물 commit  (COMPOUND-015 fidelity-violation)
  2. reports VIEW-*.md stale (COMPOUND-016 process-omission)

설치 (opt-in):
  git config core.hooksPath .githooks

Usage:
  python scripts/precommit_check.py                 # git staged 파일 대상
  python scripts/precommit_check.py --files a b c    # 명시 목록 (테스트)
  python scripts/precommit_check.py --no-views        # VIEW-stale 검사 생략
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]

# COMPOUND-015 — 부산물 패턴 (보수적 enum, false positive 방지)
# debug_* 는 정당한 코드(debug_utils.py 등)와 충돌 가능해 의도적으로 제외.
ARTIFACT_PATTERNS = [
    re.compile(r"(^|/)tmp_[^/]*"),       # tmp_blocks/, tmp_foo.md (경로 어디든 tmp_ 세그먼트)
    re.compile(r"(^|/)scratch_[^/]*"),   # scratch_*/ (COMPOUND-015)
    re.compile(r"(^|/)tmp/"),            # tmp/ 디렉토리
    re.compile(r"(^|/)_workspace(/|$)"), # _workspace/ (COMPOUND-015)
    re.compile(r"_tmp(\.[^/.]+)?$"),     # foo_tmp, foo_tmp.md
    re.compile(r"\.tmp$"),               # foo.tmp
]


def find_blocked_artifacts(paths: list[str]) -> list[str]:
    """staged 경로 중 부산물 패턴에 걸리는 것 (COMPOUND-015)."""
    blocked = []
    for p in paths:
        norm = p.replace("\\", "/").strip()
        if not norm:
            continue
        if any(pat.search(norm) for pat in ARTIFACT_PATTERNS):
            blocked.append(norm)
    return blocked


def staged_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=str(ROOT), text=True
        )
        return [l for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def tracked_files() -> list[str]:
    """git ls-files — repo 의 모든 tracked 파일 (CI --all 모드, TASK-152)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(ROOT), text=True
        )
        return [l for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def views_stale() -> bool:
    """reports VIEW-*.md 가 stale 인지 (COMPOUND-016). generate_report_views --check 재사용."""
    try:
        rc = subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "generate_report_views.py"), "--check"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return rc != 0
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pre-commit 게이트 (COMPOUND-015/016 차단)")
    parser.add_argument("--files", nargs="*", default=None, help="검사할 경로 목록 (기본: git staged)")
    parser.add_argument("--all", action="store_true", help="tracked 파일 전체 스캔 (CI 강제, TASK-152)")
    parser.add_argument("--no-views", action="store_true", help="VIEW-stale 검사 생략")
    args = parser.parse_args(argv)

    if args.files is not None:
        paths = args.files
    elif args.all:
        paths = tracked_files()
    else:
        paths = staged_files()
    problems: list[str] = []

    blocked = find_blocked_artifacts(paths)
    if blocked:
        problems.append(
            "부산물(tmp) 파일 staged (COMPOUND-015): " + ", ".join(blocked)
            + "\n  → `git restore --staged <file>` 후 제거하고 다시 commit."
        )

    if not args.no_views and views_stale():
        problems.append(
            "reports VIEW-*.md stale (COMPOUND-016)"
            "\n  → `python scripts/generate_report_views.py` 후 다시 commit."
        )

    if problems:
        print("pre-commit BLOCKED — 알려진 결함 재발 차단:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
