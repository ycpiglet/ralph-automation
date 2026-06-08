#!/usr/bin/env python3
"""Cycle collaboration gate (TASK-204, CYCLE-072).

The missing *trigger*: §16 Collaboration Mandate defines grade->subagent policy,
but nothing computed the grade from a cycle's actual changes, so the policy was
never auto-fired and Lead self-reviewed everything. This script closes that gap.

Given a cycle's changed files (or a git diff range), it:
  1. Classifies risk grade from file paths (risk-based, deterministic).
  2. Maps grade -> required collaboration via collab_log.GRADE_POLICY
     (which perspective-subagents MUST be dispatched).
  3. Maps file surfaces -> required worker roles (`/call` targets).
  4. Emits the required cycle-close artifacts + the exact commands to invoke
     the machinery (subagent_dispatch / agent_retro / agent_seminar).

It is advisory output (exit 0); enforcement of the artifacts lives in
check_agent_docs.py (REVIEW must show collaboration evidence for Critical/High).

Usage:
  python scripts/cycle_gate.py --diff origin/main         # auto from git diff
  python scripts/cycle_gate.py --changed public/app.js Managed database/functions/x/index.ts
  python scripts/cycle_gate.py --diff origin/main --format json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:  # Windows 콘솔 cp949 에서도 한글 stdout 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from collab_log import GRADE_POLICY, CONTEXT_TIERS  # reuse single source of truth
    from model_routing import select_model
except Exception:  # pragma: no cover - fallback if import path differs
    GRADE_POLICY = {
        "Critical": {"mode": "council", "subagents": ["reviewer", "auditor", "skeptic"], "tier": "T2", "model": "opus"},
        "High":     {"mode": "review-adversarial", "subagents": ["reviewer", "skeptic"], "tier": "T1", "model": "sonnet"},
        "Medium":   {"mode": "review", "subagents": ["reviewer"], "tier": "T0", "model": "sonnet"},
        "Low":      {"mode": "self", "subagents": [], "tier": "T0", "model": "haiku"},
    }
    CONTEXT_TIERS = {}

    def select_model(grade: str, *, changed_files=None, **_kwargs):
        return {
            "grade": grade,
            "policy_tier": GRADE_POLICY.get(grade, GRADE_POLICY["Medium"])["model"],
            "selected_tier": GRADE_POLICY.get(grade, GRADE_POLICY["Medium"])["model"],
            "signals": [],
            "reason": "fallback grade policy",
        }

GRADE_ORDER = ["Low", "Medium", "High", "Critical"]

DB_RE = re.compile(r"(^|/)Managed database/|(^|/)migrations?/|\.sql$|(^|/)schema\.sql$")
APP_AUTH_RE = re.compile(
    r"(^|/)(public|src)/.*(doLogin|(^|[-_/])auth([-_/]|$)|"
    r"(^|[-_/])session([-_/]|$)|user_profiles|service.?role|(^|[-_/])row-level policy([-_/]|$))",
    re.I,
)
GOVERNANCE_RE = re.compile(
    r"(^|/)scripts/check_agent_docs\.py|(^|/)\.claude/settings.*\.json|"
    r"(^|/)scripts/cycle_gate\.py|(^|/)scripts/session_start_hook\.py|"
    r"(^|/)scripts/auto_merge\.py|(^|/)AGENTS\.md$|(^|/)CLAUDE\.md$|roles\.yml$"
)
FRONTEND_RE = re.compile(r"(^|/)public/.*\.(js|html|css)$")
SCRIPT_RE = re.compile(r"(^|/)scripts/.*\.py$")
DOC_RE = re.compile(r"\.md$|(^|/)agents/.*/(retros|reviews|meetings|notes|test_cases)/")

# Risk-based path classification. First match (highest in list) wins per file;
# cycle grade = max across all changed files.
PATH_RULES = [
    # (grade, compiled regex, human reason)
    ("Critical", DB_RE,
     "DB/row-level policy/마이그레이션 — 보안 경계·데이터. 독립 감사(auditor) 필수"),
    # 인증/세션/권한 키워드는 *앱 소스 경로*에서만 Critical — 파일명(예: session_start_hook.py)
    # 오탐 방지. 프론트 auth(public/app.js doLogin)는 아래 frontend 규칙으로 최소 High.
    ("Critical", APP_AUTH_RE,
     "앱 인증/세션/권한 surface — 독립 감사 필수"),
    ("High", GOVERNANCE_RE,
     "거버넌스 게이트/훅 변경 — 적대 검토(skeptic) 필요"),
    ("High", FRONTEND_RE,
     "사용자 대면 프론트 — 검토 + Beta 라운드 권장"),
    ("Medium", SCRIPT_RE,
     "스크립트/툴링 — 검토"),
    ("Low", DOC_RE,
     "문서/기록 — self + 로그"),
]


def _append_unique(items: list[str], values: list[str]) -> None:
    for value in values:
        if value not in items:
            items.append(value)


def normalize_path(path: str) -> str:
    """Normalize repo paths before regex classification (Windows-safe)."""
    return re.sub(r"/+", "/", str(path).strip().strip('"').replace("\\", "/"))


def required_worker_roles(changed: list[str]) -> list[str]:
    """Return canonical worker roles to call for the changed surfaces.

    Worker roles (backend/uiux/beta-tester/...) are orthogonal to subagent
    perspective roles (reviewer/auditor/skeptic). `cycle_gate` must not mix
    these; otherwise it tells callers to dispatch impossible subagent roles.
    """
    roles: list[str] = []
    for raw_path in changed:
        path = normalize_path(raw_path)
        if DB_RE.search(path) or APP_AUTH_RE.search(path):
            _append_unique(roles, ["backend", "independent-auditor"])
        if GOVERNANCE_RE.search(path):
            _append_unique(roles, ["managing-partner", "independent-auditor", "doc-steward"])
        if FRONTEND_RE.search(path):
            _append_unique(roles, ["uiux", "beta-tester"])
        if SCRIPT_RE.search(path):
            _append_unique(roles, ["qa"])
        if DOC_RE.search(path):
            _append_unique(roles, ["doc-steward"])
    return roles


def classify_file(path: str) -> tuple[str, str]:
    normalized = normalize_path(path)
    for grade, rx, reason in PATH_RULES:
        if rx.search(normalized):
            return grade, reason
    return "Medium", "분류 기본값(Medium) — 명시 규칙 미매칭"


def _co(cmd: list[str]) -> str:
    return subprocess.check_output(
        cmd, cwd=str(REPO_ROOT), text=True, encoding="utf-8", errors="replace",
        stderr=subprocess.DEVNULL,
    )


def _git_changed(diff_base: str) -> list[str]:
    files: list[str] = []

    def add_many(items: list[str]) -> None:
        for item in items:
            p = normalize_path(item)
            if p and p not in files:
                files.append(p)

    for cmd in (
        ["git", "diff", "--name-only", f"{diff_base}...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ):
        try:
            out = _co(cmd)
            add_many([l.strip() for l in out.splitlines() if l.strip()])
        except Exception:
            continue

    try:
        out = _co(["git", "status", "--porcelain"])
        status_files = []
        for line in out.splitlines():
            p = line[3:].strip() if len(line) > 3 else ""
            if "->" in p:  # rename
                p = p.split("->")[-1].strip()
            if p:
                status_files.append(p)
        add_many(status_files)
    except Exception:
        pass
    return files


def _git_diff_line_count(diff_base: str) -> int:
    total = 0
    try:
        out = _co(["git", "diff", "--numstat", f"{diff_base}...HEAD"])
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                for raw in parts[:2]:
                    if raw.isdigit():
                        total += int(raw)
    except Exception:
        pass
    try:
        out = _co(["git", "diff", "--numstat"])
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                for raw in parts[:2]:
                    if raw.isdigit():
                        total += int(raw)
    except Exception:
        pass
    return total


def evaluate(changed: list[str], *, prompt: str = "", diff_lines: int = 0) -> dict:
    normalized_changed = []
    for path in changed:
        normalized = normalize_path(path)
        if normalized and normalized not in normalized_changed:
            normalized_changed.append(normalized)
    per_file = [(f, *classify_file(f)) for f in normalized_changed]
    if per_file:
        grade = max((g for _, g, _ in per_file), key=lambda g: GRADE_ORDER.index(g))
    else:
        grade = "Low"
    policy = GRADE_POLICY.get(grade, GRADE_POLICY["Medium"])
    touches_frontend = any(FRONTEND_RE.search(f) for f in normalized_changed)

    required_subagents = list(policy["subagents"])
    workers = required_worker_roles(normalized_changed)
    routing = select_model(
        grade,
        prompt=prompt,
        changed_files=normalized_changed,
        diff_lines=diff_lines,
    )
    artifacts = ["REVIEW-{NNN}.md"]
    if grade in ("Critical", "High"):
        artifacts.append("협업 evidence (collab_log record 또는 subagent dispatch 이벤트)")
    if touches_frontend:
        artifacts.append("BTC-{NNN} (Beta 라운드 — 사용자 대면 변경)")
    artifacts.append("RETRO (cycle close — agent_retro)")

    return {
        "grade": grade,
        "mode": policy["mode"],
        "context_tier": policy["tier"],
        "required_subagents": required_subagents,
        "required_worker_roles": workers,
        "required_artifacts": artifacts,
        "routing": routing,
        "diff_lines": int(diff_lines or 0),
        "touches_frontend": touches_frontend,
        "per_file": [{"file": f, "grade": g, "reason": r} for f, g, r in per_file],
    }


def _print_human(result: dict) -> None:
    g = result["grade"]
    print(f"[cycle_gate] 등급 {g} / 협업 모드 {result['mode']} / 컨텍스트 {result['context_tier']}")
    routing = result.get("routing") or {}
    if routing:
        signals = ",".join(routing.get("signals") or []) or "-"
        print(
            f"  모델 라우팅: selected={routing.get('selected_tier')} "
            f"policy={routing.get('policy_tier')} signals={signals}"
        )
    subs = result["required_subagents"]
    if subs:
        print(f"  필수 서브에이전트 dispatch: {', '.join(subs)}")
        print(
            "    → 표준 프롬프트: python scripts/subagent_dispatch.py --role <role> "
            f"--task-id <ID> --intent \"...\" --model auto --grade {g} --emit-call"
        )
        print(f"    → 그다음 Agent 툴로 실제 호출(단일 세션 §15.7: subagent 호출이 self-review 를 대체하지 않음)")
    else:
        print("  서브에이전트: 불요(Low) — self + collab 로그만")
    workers = result["required_worker_roles"]
    if workers:
        print(f"  필수 worker role /call: {', '.join(workers)}")
        print("    → python scripts/agent_orchestrator.py call <role> \"<intent>\" --task <TASK-NNN>")
    print("  필수 산출물:")
    for a in result["required_artifacts"]:
        print(f"    - {a}")
    print("  cycle close: python scripts/agent_retro.py run --all  +  python scripts/promote_retro_forward.py (feed-forward)")
    if result["per_file"]:
        print("  변경 파일 분류:")
        for pf in result["per_file"]:
            print(f"    [{pf['grade']:8}] {pf['file']}  — {pf['reason']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cycle collaboration trigger gate")
    ap.add_argument("--changed", nargs="*", default=None, help="changed file paths")
    ap.add_argument("--diff", metavar="BASE", help="git diff base (e.g. origin/main)")
    ap.add_argument("--prompt", default="", help="optional prompt text for model routing signals")
    ap.add_argument("--diff-lines", type=int, default=None,
                    help="override changed line count for model routing")
    ap.add_argument("--format", choices=["human", "json"], default="human")
    args = ap.parse_args(argv)

    if args.diff:
        changed = _git_changed(args.diff)
        diff_lines = _git_diff_line_count(args.diff) if args.diff_lines is None else args.diff_lines
    elif args.changed is not None:
        changed = args.changed
        diff_lines = 0 if args.diff_lines is None else args.diff_lines
    else:
        changed = _git_changed("origin/main")
        diff_lines = _git_diff_line_count("origin/main") if args.diff_lines is None else args.diff_lines

    result = evaluate(changed, prompt=args.prompt, diff_lines=diff_lines)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
