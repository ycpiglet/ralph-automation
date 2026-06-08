#!/usr/bin/env python3
"""BRIEF/PLAN reports 필터링 CLI — YAML frontmatter 기반.

TASK-090 reports indexing 으로 도입. 누적된 `agents/lead_engineer/reports/` 의
BRIEF/PLAN 기록을 검색 가능하게 한다.

Usage:
  python scripts/query_reports.py
  python scripts/query_reports.py --kind BRIEF
  python scripts/query_reports.py --kind PLAN --audience CEO
  python scripts/query_reports.py --related-task TASK-089
  python scripts/query_reports.py --tag reporting
  python scripts/query_reports.py --decision-topic archive
  python scripts/query_reports.py --date-from 2026-05-01 --date-to 2026-05-31
  python scripts/query_reports.py --format json

frontmatter 가 없는 파일(README, INDEX, VIEW-*)은 제외된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "agents" / "lead_engineer" / "reports"


def parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        end = text.find("\n---", 4)
        if end == -1:
            return None
    body = text[4:end]
    result: dict = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
                result[key] = items
        elif (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result


def load_reports() -> list[tuple[Path, dict]]:
    results: list[tuple[Path, dict]] = []
    if not REPORTS_DIR.exists():
        return results
    for path in sorted(REPORTS_DIR.glob("*.md")):
        if path.name in {"README.md", "INDEX.md"} or path.name.startswith("VIEW-"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm is None:
            continue
        if fm.get("type") != "report":
            continue
        results.append((path, fm))
    return results


def match_filter(fm: dict, args: argparse.Namespace) -> bool:
    if args.kind and fm.get("kind") != args.kind:
        return False
    if args.audience and fm.get("audience") != args.audience:
        return False
    if args.scale and fm.get("scale") != args.scale:
        return False
    if args.related_task and fm.get("related_task") != args.related_task:
        return False
    if args.related_cycle and fm.get("related_cycle") != args.related_cycle:
        return False
    if args.related_meeting and fm.get("related_meeting") != args.related_meeting:
        return False
    if args.tag:
        tags = fm.get("tags") or []
        if not isinstance(tags, list) or args.tag not in tags:
            return False
    if args.decision_topic:
        topics = fm.get("decision_topics") or []
        if not isinstance(topics, list) or args.decision_topic not in topics:
            return False
    if args.date_from and str(fm.get("date", "")) < args.date_from:
        return False
    if args.date_to and str(fm.get("date", "")) > args.date_to:
        return False
    return True


def render_table(rows: list[tuple[Path, dict]]) -> str:
    if not rows:
        return "(no matching reports)\n"
    headers = ["ID", "Kind", "Date", "Audience", "Scale", "Title", "Related"]
    out_rows = []
    for _, fm in rows:
        related = (
            fm.get("related_task")
            or fm.get("related_cycle")
            or fm.get("related_meeting")
            or "자가발생"
        )
        out_rows.append([
            str(fm.get("id", "")),
            str(fm.get("kind", "")),
            str(fm.get("date", "")),
            str(fm.get("audience", "")),
            str(fm.get("scale", "")),
            str(fm.get("title", "")),
            str(related),
        ])
    widths = [max(len(h), *(len(r[i]) for r in out_rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in out_rows:
        lines.append("  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines) + "\n"


def render_json(rows: list[tuple[Path, dict]]) -> str:
    payload = []
    for path, fm in rows:
        payload.append({
            "path": str(path.relative_to(ROOT)),
            "frontmatter": fm,
        })
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query BRIEF/PLAN reports by frontmatter fields.",
    )
    parser.add_argument("--kind", choices=["BRIEF", "PLAN"], default=None)
    parser.add_argument("--audience", choices=["CEO", "agent", "mixed"], default=None)
    parser.add_argument("--scale", choices=["mini", "standard", "full"], default=None)
    parser.add_argument("--related-task", default=None, help="e.g. TASK-089")
    parser.add_argument("--related-cycle", default=None, help="e.g. CYCLE-012")
    parser.add_argument("--related-meeting", default=None, help="e.g. MEETING-2026-05-22-001")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--decision-topic", default=None)
    parser.add_argument("--date-from", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--date-to", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    all_reports = load_reports()
    matched = [r for r in all_reports if match_filter(r[1], args)]
    matched.sort(key=lambda r: (r[1].get("date", ""), r[1].get("id", "")), reverse=True)

    if args.format == "json":
        sys.stdout.write(render_json(matched))
    else:
        sys.stdout.write(render_table(matched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
