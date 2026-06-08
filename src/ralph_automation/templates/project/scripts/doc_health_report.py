#!/usr/bin/env python3
"""Read-only document health report for operating records.

This script is intentionally advisory. It reports likely drift for Doc Steward
checks, but it does not mutate files and exits 0 by default so it can be used in
local triage without blocking unrelated work.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

REVIEW_LIKE_TYPES = {"reply", "subagent_reply", "answer"}

# REVIEW 관행 도입 전(CYCLE < 10) 완료 사이클은 frozen legacy — REVIEW 파일이 없어도
# 가짜 리뷰 백필 대신 면제한다(COMPOUND-030: advisory 도구는 frozen 레코드 재심 금지,
# busywork 회피). >= 10 완료 사이클의 리뷰 누락은 그대로 ERROR.
REVIEW_PRACTICE_FROM = 10

TASK_REQUIRED_KEYS = {
    "type", "id", "status", "owner", "assignees", "priority", "difficulty",
    "est_hours", "est_tokens", "tags", "trigger_meeting", "audit_log",
    "created", "created_at",
}
STALE_OPEN_HOURS = 24


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str


def parse_frontmatter(text: str) -> dict[str, object] | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    meta: dict[str, object] = {}
    current_list_key: str | None = None
    for raw in parts[1].splitlines():
        line = raw.rstrip()
        if not line:
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            existing = meta.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(line[4:].strip())
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            meta[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            body = value[1:-1].strip()
            meta[key] = [p.strip() for p in body.split(",") if p.strip()] if body else []
            current_list_key = None
        else:
            meta[key] = value
            current_list_key = None
    return meta


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def latest_cycle_number(root: Path) -> int | None:
    nums: list[int] = []
    for path in (root / "agents" / "lead_engineer").glob("CYCLE-*.md"):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if match:
            nums.append(int(match.group(1)))
    return max(nums) if nums else None


def check_latest_status(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    latest = latest_cycle_number(root)
    if latest is None:
        return [Finding("ERROR", "missing-cycle", "agents/lead_engineer", "no CYCLE-*.md files found")]
    status = root / "agents" / "lead_engineer" / "STATUS.md"
    text = read_text(status)
    if f"CYCLE-{latest:03d}" not in text:
        findings.append(Finding(
            "WARN", "status-latest-cycle",
            rel(root, status),
            f"STATUS.md does not mention latest CYCLE-{latest:03d}",
        ))
    return findings


def check_missing_review_files(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    cycles_dir = root / "agents" / "lead_engineer"
    reviews_dir = cycles_dir / "reviews"
    for path in sorted(cycles_dir.glob("CYCLE-*.md")):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if not match:
            continue
        cycle_id = int(match.group(1))
        # REVIEW 관행 도입 전(CYCLE < REVIEW_PRACTICE_FROM) frozen legacy 는 면제(COMPOUND-030).
        if cycle_id < REVIEW_PRACTICE_FROM:
            continue
        text = read_text(path)
        if re.search(r"(?m)^상태:\s*완료\s*$", text):
            review = reviews_dir / f"REVIEW-{cycle_id:03d}.md"
            if not review.exists():
                findings.append(Finding(
                    "ERROR", "missing-review", rel(root, path),
                    f"completed CYCLE-{cycle_id:03d} has no reviews/REVIEW-{cycle_id:03d}.md",
                ))
    return findings


def check_role_contracts(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = root / "agents" / "roles.yml"
    text = read_text(path)
    if not text:
        return [Finding("ERROR", "missing-roles", rel(root, path), "roles.yml missing or unreadable")]
    blocks = re.split(r"(?m)^  - id:\s*", text)[1:]
    for block in blocks:
        lines = block.splitlines()
        role_id = lines[0].strip() if lines else "unknown"
        for key in ("aliases:", "skill_file:", "required_inputs:", "forbidden_inputs:", "output_contract:"):
            if key not in block:
                findings.append(Finding(
                    "ERROR", "role-contract-gap", rel(root, path),
                    f"role {role_id} missing {key}",
                ))
        match = re.search(r"(?m)^    skill_file:\s*(.+?)\s*$", block)
        if match:
            skill = match.group(1).strip()
            if not (root / skill).exists():
                findings.append(Finding(
                    "ERROR", "missing-role-skill", rel(root, path),
                    f"role {role_id} skill_file not found: {skill}",
                ))
    return findings


def check_task_frontmatter_gaps(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    task_dir = root / "agents" / "lead_engineer" / "tasks"
    for path in sorted(task_dir.glob("TASK-*.md")):
        match = re.match(r"TASK-(\d+)", path.name)
        if not match or int(match.group(1)) < 48:
            continue
        meta = parse_frontmatter(read_text(path))
        if meta is None:
            findings.append(Finding("ERROR", "task-frontmatter-missing", rel(root, path), "missing YAML frontmatter"))
            continue
        # Completed tasks are frozen canonical history — their frontmatter was already
        # gated by check_agent_docs at completion. Re-flagging them is advisory noise
        # (e.g. empty assignees:[] on a closed task). Only surface gaps on ACTIVE tasks.
        if str(meta.get("status", "")).strip() == "완료":
            continue
        missing = sorted(k for k in TASK_REQUIRED_KEYS if k not in meta or meta[k] in ("", []))
        if missing:
            findings.append(Finding(
                "ERROR", "task-frontmatter-gap", rel(root, path),
                "missing required frontmatter keys: " + ", ".join(missing),
            ))
    return findings


def parse_iso(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def message_files(root: Path) -> list[Path]:
    base = root / "agents" / "messages"
    out: list[Path] = []
    for sub in ("inbox", "archive", "samples"):
        d = base / sub
        if d.is_dir():
            out.extend(sorted(p for p in d.iterdir() if p.suffix == ".md" and not p.name.startswith(".")))
    return out


def check_message_health(root: Path, now: dt.datetime | None = None) -> list[Finding]:
    findings: list[Finding] = []
    parsed: dict[str, tuple[Path, dict[str, object]]] = {}
    for path in message_files(root):
        meta = parse_frontmatter(read_text(path))
        if not meta:
            findings.append(Finding("ERROR", "message-frontmatter", rel(root, path), "missing or invalid frontmatter"))
            continue
        msg_id = meta.get("id")
        if isinstance(msg_id, str) and msg_id:
            parsed[msg_id] = (path, meta)

    for msg_id, (path, meta) in parsed.items():
        msg_type = meta.get("type")
        in_reply_to = meta.get("in_reply_to")
        if isinstance(in_reply_to, list):
            in_reply_to = ""
        if msg_type in REVIEW_LIKE_TYPES and not in_reply_to:
            findings.append(Finding("ERROR", "message-reply-link", rel(root, path), f"{msg_type} missing in_reply_to"))
        if isinstance(in_reply_to, str) and in_reply_to and in_reply_to not in parsed:
            findings.append(Finding(
                "ERROR", "message-orphan", rel(root, path),
                f"in_reply_to references unknown message id {in_reply_to}",
            ))
        if meta.get("status") == "open" and "/samples/" not in rel(root, path):
            ts = parse_iso(meta.get("ts"))
            if ts is not None:
                ref = now or dt.datetime.now(ts.tzinfo or dt.timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ref.tzinfo)
                age = (ref - ts).total_seconds() / 3600.0
                if age > STALE_OPEN_HOURS:
                    findings.append(Finding(
                        "WARN", "message-stale-open", rel(root, path),
                        f"open message stale ({age:.1f}h > {STALE_OPEN_HOURS}h)",
                    ))
    return findings


def non_code_lines(text: str):
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            yield line


def check_markdown_links(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    latest = latest_cycle_number(root)
    core_paths = [
        root / "AGENTS.md",
        root / "README.md",
        root / "AGENT_RUNTIME.md",
        root / "CLAUDE.md",
        root / "GEMINI.md",
        root / "CURSOR.md",
        root / "agents" / "lead_engineer" / "STATUS.md",
        root / "agents" / "lead_engineer" / "tasks" / "INDEX.md",
    ]
    if latest is not None:
        core_paths.append(root / "agents" / "lead_engineer" / f"CYCLE-{latest:03d}.md")
        core_paths.append(root / "agents" / "lead_engineer" / "reviews" / f"REVIEW-{latest:03d}.md")
    for role_dir in (root / "agents").glob("*"):
        skill = role_dir / "SKILL.md"
        if skill.exists():
            core_paths.append(skill)

    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for path in sorted(set(core_paths)):
        if not path.exists():
            continue
        for line in non_code_lines(read_text(path)):
            for match in link_re.finditer(line):
                target = match.group(1).strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = target.strip("<>")
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue
                resolved = (path.parent / target_path).resolve()
                if not resolved.exists():
                    findings.append(Finding(
                        "WARN", "markdown-link-missing", rel(root, path),
                        f"link target missing: {target}",
                    ))
    return findings


def collect_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_latest_status(root))
    findings.extend(check_missing_review_files(root))
    findings.extend(check_role_contracts(root))
    findings.extend(check_task_frontmatter_gaps(root))
    findings.extend(check_message_health(root))
    findings.extend(check_markdown_links(root))
    return findings


def overall_status(findings: list[Finding]) -> str:
    if any(f.severity == "ERROR" for f in findings):
        return "R"
    if any(f.severity == "WARN" for f in findings):
        return "Y"
    return "G"


def render_text(findings: list[Finding]) -> str:
    status = overall_status(findings)
    counts = {
        "ERROR": sum(1 for f in findings if f.severity == "ERROR"),
        "WARN": sum(1 for f in findings if f.severity == "WARN"),
        "INFO": sum(1 for f in findings if f.severity == "INFO"),
    }
    lines = [
        "Doc Health Report",
        f"Status: {status}",
        f"Findings: {len(findings)} (ERROR={counts['ERROR']}, WARN={counts['WARN']}, INFO={counts['INFO']})",
    ]
    for finding in findings:
        lines.append(f"[{finding.severity}] {finding.code} {finding.path}: {finding.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only document health report.")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--fail-on-error", action="store_true", help="exit 1 when ERROR findings exist")
    args = parser.parse_args(argv)

    findings = collect_findings(ROOT)
    if args.json:
        payload = {"status": overall_status(findings), "findings": [asdict(f) for f in findings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(findings))
    return 1 if args.fail_on_error and any(f.severity == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
