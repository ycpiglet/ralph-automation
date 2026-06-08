"""
프로젝트 표준 타임스탬프 생성기 — OS 비종속.

이 스크립트는 본 프로젝트의 **canonical 시각 출처**다.
Windows / macOS / Linux 어디서나 동일한 ISO 8601 출력을 보장한다.

사용:
  python scripts/now.py              # 로컬 시각 + 타임존 오프셋 (예: 2026-05-21T11:11:14+09:00)
  python scripts/now.py --utc        # UTC + Z (예: 2026-05-21T02:11:14Z)
  python scripts/now.py --date       # 날짜만 (예: 2026-05-21)
  python scripts/now.py --epoch      # Unix epoch 초

쓰는 곳:
  - MEETING/TASK/AUDIT-LOG/Review/Compound 의 모든 신규 시각 필드
  - LLM 에이전트가 시각 필드를 채우기 전 반드시 이 스크립트를 한 번 호출
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys


def _to_iso_with_colon_offset(dt: _dt.datetime) -> str:
    """%z가 반환하는 +0900 형식을 ISO 8601 표준의 +09:00 형식으로 변환."""
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    if len(s) >= 5 and s[-5] in "+-":
        s = s[:-2] + ":" + s[-2:]
    return s


def local_iso() -> str:
    """로컬 시간대 기준 ISO 8601 (예: 2026-05-21T11:11:14+09:00)."""
    now = _dt.datetime.now(_dt.timezone.utc).astimezone()
    return _to_iso_with_colon_offset(now)


def utc_iso() -> str:
    """UTC 기준 ISO 8601 with Z (예: 2026-05-21T02:11:14Z)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_only() -> str:
    """로컬 시간대의 날짜 (예: 2026-05-21)."""
    return _dt.datetime.now(_dt.timezone.utc).astimezone().strftime("%Y-%m-%d")


def epoch_seconds() -> str:
    return str(int(_dt.datetime.now(_dt.timezone.utc).timestamp()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project-wide canonical timestamp source (ISO 8601, OS-agnostic)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--utc", action="store_true", help="UTC + Z suffix")
    group.add_argument("--date", action="store_true", help="date only (YYYY-MM-DD)")
    group.add_argument("--epoch", action="store_true", help="Unix epoch seconds")
    args = parser.parse_args(argv)

    if args.utc:
        print(utc_iso())
    elif args.date:
        print(date_only())
    elif args.epoch:
        print(epoch_seconds())
    else:
        print(local_iso())
    return 0


if __name__ == "__main__":
    sys.exit(main())
