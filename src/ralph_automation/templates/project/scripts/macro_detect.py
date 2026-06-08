#!/usr/bin/env python3
"""macro_detect — 반복 요청 감지 → 함수화 *제안만* (TASK-228, MEETING-2026-06-04-001).

Owner 가 반복적으로 요청하는 작업을 감지해 "이걸 함수/스크립트로 만들지" 를 **제안**한다.
seminar 두 관점(cry-wolf COMPOUND-030 + 프라이버시)에 따라 보수적으로 설계:

  - **propose-only**: 후보를 보고만 한다. TASK/branch/PR/코드/skill 을 절대 자동 생성하지 않는다.
    함수화(skill/.claude 생성)는 R3 — 정상 plan→approve 루프로만.
  - **고임계(cry-wolf 억제)**: 같은 정규화 의도가 ≥3회 AND ≥2개의 서로 다른 날짜에 나타나야 발화.
    자동 튜닝 없는 고정 상수.
  - **프라이버시**: 입력은 repo 운영 기록(TASK/MEETING frontmatter)만. raw 프롬프트·키스트로크·
    시크릿은 대상이 아니다(읽지도 않는다).
  - auto_runner 입력이 아니다(무인 실행 경로와 분리).

사용:
  python scripts/macro_detect.py            # 반복 의도 후보 제안(없으면 침묵에 가깝게)
  python scripts/macro_detect.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"
MEETINGS_DIR = ROOT / "agents" / "lead_engineer" / "meetings"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 고정 임계 — 자동 튜닝 없음(COMPOUND-030 cry-wolf 억제).
MIN_COUNT = 3       # 같은 정규화 토큰 최소 발생 수
MIN_DAYS = 2        # 최소 서로 다른 날짜 수(한 세션 폭주를 반복으로 오인 방지)
# 너무 일반적이라 신호가 안 되는 토큰(잡음 제거).
STOPWORDS = {"backlog", "planning", "verification", "automation", "runtime", "phase2", "phase3", "phase4"}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _frontmatter_tags_date(text: str) -> tuple[list[str], str | None]:
    """frontmatter 에서 tags(list)와 날짜(created/date)만 뽑는다(최소 파싱)."""
    if not text.startswith("---\n"):
        return [], None
    end = text.find("\n---", 4)
    if end == -1:
        return [], None
    body = text[4:end]
    tags: list[str] = []
    date: str | None = None
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("tags:"):
            val = s.partition(":")[2].strip()
            if val.startswith("[") and val.endswith("]"):
                tags = [t.strip().strip("'\"") for t in val[1:-1].split(",") if t.strip()]
        elif (s.startswith("created:") or s.startswith("date:")) and date is None:
            m = _DATE_RE.search(s)
            if m:
                date = m.group(0)
    return tags, date


def gather_signals() -> list[dict]:
    """repo 운영 기록(TASK/MEETING)에서 (token, date, source) 신호만 수집.

    프라이버시: agents/ 의 구조화 기록만 읽는다 — 프롬프트/시크릿은 대상 아님.
    """
    signals: list[dict] = []
    for folder, glob in ((TASKS_DIR, "TASK-*.md"), (MEETINGS_DIR, "MEETING-*.md")):
        if not folder.is_dir():
            continue
        for path in folder.glob(glob):
            if path.name.endswith("-result.md"):
                continue
            tags, date = _frontmatter_tags_date(path.read_text(encoding="utf-8"))
            for tag in tags:
                token = tag.lower().strip()
                if token and token not in STOPWORDS:
                    signals.append({"token": token, "date": date, "source": path.name})
    return signals


def detect(signals: list[dict], min_count: int = MIN_COUNT, min_days: int = MIN_DAYS) -> list[dict]:
    """임계(≥min_count 발생 AND ≥min_days 서로 다른 날짜)를 넘는 반복 의도 후보."""
    by_token: dict[str, dict] = defaultdict(lambda: {"count": 0, "dates": set(), "sources": []})
    for s in signals:
        rec = by_token[s["token"]]
        rec["count"] += 1
        if s.get("date"):
            rec["dates"].add(s["date"])
        rec["sources"].append(s["source"])
    out = []
    for token, rec in by_token.items():
        if rec["count"] >= min_count and len(rec["dates"]) >= min_days:
            out.append({
                "token": token,
                "count": rec["count"],
                "days": len(rec["dates"]),
                "sources": sorted(set(rec["sources"]))[:6],
            })
    out.sort(key=lambda c: (-c["count"], -c["days"], c["token"]))
    return out


def render(candidates: list[dict]) -> str:
    lines = [
        "# macro_detect — 반복 의도 후보 (propose-only)",
        "",
        f"임계: ≥{MIN_COUNT}회 AND ≥{MIN_DAYS}일. 입력=TASK/MEETING 기록만(프롬프트·시크릿 비대상).",
        "**제안일 뿐 — 자동 생성 안 함.** 함수화(skill/스크립트)는 R3(정상 plan→approve).",
        "",
    ]
    if not candidates:
        lines.append("후보 없음 — 임계 미만(침묵). cry-wolf 억제(COMPOUND-030).")
        return "\n".join(lines) + "\n"
    for c in candidates:
        lines.append(f"- **{c['token']}** — {c['count']}회 / {c['days']}일 "
                     f"→ 함수화/스크립트화 검토 제안? (예: {', '.join(c['sources'][:3])})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="반복 요청 감지 → 함수화 제안(propose-only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    candidates = detect(gather_signals())
    if args.json:
        print(json.dumps({"candidates": candidates, "min_count": MIN_COUNT, "min_days": MIN_DAYS},
                         ensure_ascii=False, indent=2))
    else:
        print(render(candidates), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
