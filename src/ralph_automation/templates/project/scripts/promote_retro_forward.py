#!/usr/bin/env python3
"""RETRO §5 Forward Actions 자동 승격 (TASK-068).

각 RETRO 파일의 §5 Forward Actions 표를 파싱해 TASK / Compound / SKILL 후보를
한 곳으로 모은다. CYCLE 종료 시점에 다음 CYCLE 후보 목록 자동 구성용.

본 1차 구현은 read-only 보고서 생성에 한정. 자동 TASK 신규 생성은 후속.

Usage:
  python scripts/promote_retro_forward.py
  python scripts/promote_retro_forward.py --since 2026-05-01
  python scripts/promote_retro_forward.py --kind TASK
  python scripts/promote_retro_forward.py --format json
  python scripts/promote_retro_forward.py --out agents/lead_engineer/RETRO-FORWARD-BACKLOG.md

표 헤더 (TEMPLATE.md 기준, check_retros 가 무결성 보장):

| 종류 | 제안 | 우선순위 | Owner 제안 | 근거 |
|------|------|----------|-----------|------|
| TASK 후보 | ... | High/Medium/Low | 역할 | §N |
| Compound 후보 | ... | — | Lead Engineer | §2 |
| SKILL 갱신 | ... | — | 본인 | §4 |

종류 컬럼이 "TASK 후보" / "Compound 후보" / "SKILL 갱신" 중 하나면 후보로 인정.
"(없음)" 행은 skip.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]

KIND_TASK = "TASK 후보"
KIND_COMPOUND = "Compound 후보"
KIND_SKILL = "SKILL 갱신"
VALID_KINDS = {KIND_TASK, KIND_COMPOUND, KIND_SKILL}
SECTION_HEADER = "## §5 Forward Actions"


@dataclass
class ForwardItem:
    """RETRO §5 Forward Actions 한 행.

    `tier`는 TASK-133에서 추가된 컬럼이며 SKILL 갱신만 T0/T1/T2/T3로 채워지고
    그 외 종류·구 포맷(5컬럼) RETRO는 `'—'`로 자동 채워 호환된다.
    """

    retro_path: str
    role: str
    period_end: str
    kind: str
    proposal: str
    priority: str
    owner: str
    basis: str
    tier: str = "—"

    def to_dict(self) -> dict:
        return {
            "retro_path": self.retro_path,
            "role": self.role,
            "period_end": self.period_end,
            "kind": self.kind,
            "proposal": self.proposal,
            "priority": self.priority,
            "owner": self.owner,
            "basis": self.basis,
            "tier": self.tier,
        }


def parse_frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    return m.group(1).strip() if m else ""


def parse_forward_section(retro_path: Path) -> list[ForwardItem]:
    """RETRO 파일의 §5 Forward Actions 표를 파싱."""
    text = retro_path.read_text(encoding="utf-8")
    role = parse_frontmatter_value(text, "role")
    period_end = parse_frontmatter_value(text, "period_end")

    idx = text.find(SECTION_HEADER)
    if idx == -1:
        return []
    # §5 부터 다음 ## 또는 EOF 까지
    rest = text[idx + len(SECTION_HEADER):]
    next_section = re.search(r"(?m)^## ", rest)
    section_body = rest[:next_section.start()] if next_section else rest

    # repo 밖 경로(시험 등)일 때는 절대 경로 그대로 사용 (fallback).
    try:
        rel_path = str(retro_path.relative_to(ROOT))
    except ValueError:
        rel_path = str(retro_path)

    items: list[ForwardItem] = []
    for line in section_body.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        # 6컬럼 = 신 포맷(Tier 포함, TASK-133). 5컬럼 = 구 포맷(Tier 없음, 호환).
        if len(cells) == 6:
            kind, proposal, tier, priority, owner, basis = cells
        elif len(cells) == 5:
            kind, proposal, priority, owner, basis = cells
            tier = "—"
        else:
            continue
        if kind in {"종류", "------"} or kind.startswith("---"):
            continue
        if proposal in {"(없음)", "—", "-"} and kind in {"(없음)", "—", "-"}:
            continue
        if kind not in VALID_KINDS:
            continue
        items.append(ForwardItem(
            retro_path=rel_path,
            role=role,
            period_end=period_end,
            kind=kind,
            proposal=proposal,
            priority=priority,
            owner=owner,
            basis=basis,
            tier=tier,
        ))
    return items


def collect_all(since: str | None = None) -> list[ForwardItem]:
    """모든 RETRO 파일을 훑어 Forward Items 수집."""
    out: list[ForwardItem] = []
    for path in sorted((ROOT / "agents").glob("*/retros/RETRO-*.md")):
        if path.name == "TEMPLATE.md":
            continue
        if since:
            period_end = parse_frontmatter_value(path.read_text(encoding="utf-8"), "period_end")
            if period_end and period_end < since:
                continue
        out.extend(parse_forward_section(path))
    return out


def render_markdown(items: list[ForwardItem]) -> str:
    if not items:
        return "_(no forward actions found)_\n"

    # 종류별 그룹핑
    grouped: dict[str, list[ForwardItem]] = {KIND_TASK: [], KIND_COMPOUND: [], KIND_SKILL: []}
    for it in items:
        grouped.setdefault(it.kind, []).append(it)

    lines: list[str] = []
    lines.append("# RETRO Forward Actions — 다음 CYCLE 후보 (자동 생성)")
    lines.append("")
    lines.append(f"`scripts/promote_retro_forward.py` 가 RETRO §5 Forward Actions 표를 파싱해 생성.")
    lines.append(f"총 {len(items)} 항목 — TASK {len(grouped[KIND_TASK])} / Compound {len(grouped[KIND_COMPOUND])} / SKILL {len(grouped[KIND_SKILL])}.")
    lines.append("")

    for kind in (KIND_TASK, KIND_COMPOUND, KIND_SKILL):
        bucket = grouped.get(kind, [])
        if not bucket:
            continue
        lines.append(f"## {kind} ({len(bucket)})")
        lines.append("")
        lines.append("| Proposal | Priority | Owner | RETRO | Period End | Basis |")
        lines.append("|----------|----------|-------|-------|------------|-------|")
        for it in bucket:
            proposal = it.proposal.replace("|", "\\|")
            basis = it.basis.replace("|", "\\|")
            lines.append(f"| {proposal} | {it.priority} | {it.owner} | `{it.retro_path}` | {it.period_end} | {basis} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote RETRO §5 Forward Actions to next-cycle backlog.")
    parser.add_argument("--since", help="period_end >= YYYY-MM-DD (skip older RETROs)")
    parser.add_argument("--kind", choices=["TASK", "Compound", "SKILL"], help="filter by kind")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--out", help="write to file instead of stdout")
    args = parser.parse_args()

    items = collect_all(args.since)
    if args.kind:
        suffix_map = {"TASK": "TASK 후보", "Compound": "Compound 후보", "SKILL": "SKILL 갱신"}
        items = [i for i in items if i.kind == suffix_map[args.kind]]

    if args.format == "json":
        payload = [i.to_dict() for i in items]
        out = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        out = render_markdown(items)

    if args.out:
        (ROOT / args.out).write_text(out, encoding="utf-8")
        print(f"OK: wrote {args.out} ({len(items)} items)")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
