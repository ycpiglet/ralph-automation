#!/usr/bin/env python3
"""Collaboration mandate + logging (TASK-123).

Makes subagent collaboration the *default* for everyday work and records each
collaboration's plan / method / result so it can be observed (TASK-126),
debugged, and improved. Formalizes the ad-hoc collab log first written by
hand in TASK-048 (agents/runtime/events/collab-<date>.jsonl).

Two pieces:
  1. Grade -> collaboration policy (작업 등급별 협업 강도)
  2. Context tier policy (COLLAB-CONTEXT-STRATEGY.md 4-tier)
  3. collab event logger (계획/방식/결과)

CLI:
  python scripts/collab_log.py policy --grade High
  python scripts/collab_log.py record --task-id TASK-123 --tier T0 \\
      --method reviewer --verdict approve --tokens 38000 \\
      --plan "diff-only review" --parties implementer,reviewer
  python scripts/collab_log.py show [--task-id TASK-123]
"""

from __future__ import annotations

import argparse
import datetime as _dt
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
EVENTS_DIR = ROOT / "agents" / "runtime" / "events"

sys.path.insert(0, str(ROOT / "scripts"))
import eval_harness  # noqa: E402

# 작업 등급 -> 협업 강도 (MEETING-2026-05-27-001 결정 #3)
GRADE_POLICY = {
    "Critical": {"mode": "council", "subagents": ["reviewer", "auditor", "skeptic"], "tier": "T2", "model": "opus"},
    "High":     {"mode": "review-adversarial", "subagents": ["reviewer", "skeptic"], "tier": "T1", "model": "sonnet"},
    "Medium":   {"mode": "review", "subagents": ["reviewer"], "tier": "T0", "model": "sonnet"},
    "Low":      {"mode": "self", "subagents": [], "tier": "T0", "model": "haiku"},
}

# 컨텍스트 tier (COLLAB-CONTEXT-STRATEGY.md §2)
CONTEXT_TIERS = {
    "T0": "diff-only (변경 부분 + 직접 관련 정의)",
    "T1": "확장 (diff + 주변 함수/호출처)",
    "T2": "요약 (큰 파일 개요 먼저, 원문 요청 시)",
    "T3": "전체 (파일 전체 / 다중 파일)",
}

# 검토자 신호 -> 자동 에스컬레이션 (COLLAB-CONTEXT-STRATEGY.md §2 에스컬레이션 룰)
ESCALATION_SIGNALS = {
    "needs full-file": "T3",
    "전체 확인 필요": "T3",
    "needs full-file verification": "T3",
    "주변 확인": "T1",
    "호출처 확인": "T1",
}

VERDICT_VALUES = {"approve", "reject", "needs-changes", "abstain", "done"}
TIER_GRADE = {"T0": "Medium", "T1": "High", "T2": "Critical", "T3": "Critical"}


def policy_for_grade(grade: str) -> dict:
    """Return the collaboration policy for a task grade."""
    return GRADE_POLICY.get(grade, GRADE_POLICY["Medium"])


def escalate_tier(current_tier: str, reviewer_text: str) -> str:
    """Given a reviewer's response, return the tier to escalate to (or current)."""
    target = current_tier
    order = ["T0", "T1", "T2", "T3"]
    low = reviewer_text.lower()
    for signal, tier in ESCALATION_SIGNALS.items():
        if signal.lower() in low:
            if order.index(tier) > order.index(target):
                target = tier
    return target


def _positive_int(value) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _eval_grade(tier: str, grade: str | None) -> str:
    if grade in GRADE_POLICY:
        return str(grade)
    return TIER_GRADE.get(tier, "Medium")


def _maybe_record_eval_outcome(
    *,
    task_id: str,
    tier: str,
    grade: str | None,
    verdict: str,
    parties: list[str],
    tokens: int | None,
    baseline_verdict: str | None,
    baseline_tokens: int | None,
    eval_log_path: Path | None,
    dry_run: bool,
) -> None:
    actual_tokens = _positive_int(tokens)
    baseline = _positive_int(baseline_tokens)
    if dry_run or actual_tokens is None or baseline is None:
        return
    if not baseline_verdict or not parties:
        return
    effective_grade = _eval_grade(tier, grade)
    model = str(policy_for_grade(effective_grade).get("model", ""))
    eval_harness.record_outcome(
        task_id,
        effective_grade,
        model,
        actual_tokens,
        outcome=verdict,
        path=eval_log_path or eval_harness.EVAL_LOG,
        baseline_tokens=baseline,
        baseline_verdict=baseline_verdict,
        collab_verdict=verdict,
        collab_members=parties,
    )


def record_collaboration(task_id: str, tier: str, method: str, verdict: str,
                         plan: str = "", parties: list[str] | None = None,
                         tokens: int | None = None, findings: list[str] | None = None,
                         outcome: str = "", dry_run: bool = False,
                         baseline_verdict: str | None = None,
                         baseline_tokens: int | None = None,
                         grade: str | None = None,
                         eval_log_path: Path | None = None) -> Path:
    """Append a collaboration event to collab-<date>.jsonl."""
    if tier not in CONTEXT_TIERS:
        raise ValueError(f"tier must be one of {sorted(CONTEXT_TIERS)}, got '{tier}'")
    if verdict not in VERDICT_VALUES:
        raise ValueError(f"verdict must be one of {sorted(VERDICT_VALUES)}, got '{verdict}'")
    if not task_id:
        raise ValueError("task_id is required")
    now = _dt.datetime.now().astimezone()
    record = {
        "ts": now.isoformat(timespec="seconds"),
        "task_id": task_id,
        "tier": tier,
        "method": method,
        "verdict": verdict,
        "plan": plan,
        "parties": parties or [],
        "tokens": tokens,
        "findings": findings or [],
        "outcome": outcome,
    }
    normalized_parties = parties or []
    target = EVENTS_DIR / f"collab-{now.strftime('%Y-%m-%d')}.jsonl"
    if not dry_run:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _maybe_record_eval_outcome(
        task_id=task_id,
        tier=tier,
        grade=grade,
        verdict=verdict,
        parties=normalized_parties,
        tokens=tokens,
        baseline_verdict=baseline_verdict,
        baseline_tokens=baseline_tokens,
        eval_log_path=eval_log_path,
        dry_run=dry_run,
    )
    return target


def read_collaborations(task_id: str | None = None) -> list[dict]:
    """Read all collab events, optionally filtered by task_id."""
    out: list[dict] = []
    if not EVENTS_DIR.is_dir():
        return out
    for p in sorted(EVENTS_DIR.glob("collab-*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if task_id is None or rec.get("task_id") == task_id:
                    out.append(rec)
        except OSError:
            continue
    return out


# ---------- CLI ----------


def _cmd_policy(args: argparse.Namespace) -> int:
    p = policy_for_grade(args.grade)
    print(f"grade={args.grade}")
    print(f"  mode: {p['mode']}")
    print(f"  subagents: {', '.join(p['subagents']) or '(none — self)'}")
    print(f"  default tier: {p['tier']} ({CONTEXT_TIERS[p['tier']]})")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        path = record_collaboration(
            task_id=args.task_id, tier=args.tier, method=args.method,
            verdict=args.verdict, plan=args.plan or "",
            parties=[s.strip() for s in (args.parties or "").split(",") if s.strip()],
            tokens=args.tokens,
            findings=[s.strip() for s in (args.findings or "").split(";") if s.strip()],
            outcome=args.outcome or "", dry_run=args.dry_run,
            baseline_verdict=args.baseline_verdict,
            baseline_tokens=args.baseline_tokens,
            grade=args.grade,
            eval_log_path=args.eval_log,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    word = "would write" if args.dry_run else "wrote"
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)
    print(f"[collab] {word} {rel}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    recs = read_collaborations(args.task_id)
    if not recs:
        print(f"(no collaborations{' for ' + args.task_id if args.task_id else ''})")
        return 0
    for r in recs:
        tok = f" {r['tokens']}tok" if r.get("tokens") else ""
        print(f"{r['ts']} {r['task_id']} [{r.get('tier','?')}/{r.get('method','?')}] "
              f"-> {r.get('verdict','?')}{tok}")
        if r.get("outcome"):
            print(f"    outcome: {r['outcome']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="collab_log.py",
        description="Collaboration mandate + logging (TASK-123).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    po = sub.add_parser("policy", help="show collaboration policy for a grade")
    po.add_argument("--grade", required=True,
                    choices=["Critical", "High", "Medium", "Low"])
    po.set_defaults(func=_cmd_policy)

    re_ = sub.add_parser("record", help="record a collaboration event")
    re_.add_argument("--task-id", required=True)
    re_.add_argument("--tier", required=True, choices=sorted(CONTEXT_TIERS))
    re_.add_argument("--method", required=True)
    re_.add_argument("--verdict", required=True, choices=sorted(VERDICT_VALUES))
    re_.add_argument("--plan", default="")
    re_.add_argument("--parties", default="", help="comma list")
    re_.add_argument("--tokens", type=int)
    re_.add_argument("--findings", default="", help="semicolon list")
    re_.add_argument("--outcome", default="")
    re_.add_argument("--grade", choices=["Critical", "High", "Medium", "Low"],
                     help="eval grade for collaboration delta; defaults from tier")
    re_.add_argument("--baseline-verdict",
                     help="self/baseline verdict before collaboration; enables eval row with tokens+baseline")
    re_.add_argument("--baseline-tokens", type=int,
                     help="self/baseline token count for collaboration cost comparison")
    re_.add_argument("--eval-log", type=Path, help="eval log path for collaboration delta")
    re_.add_argument("--dry-run", action="store_true")
    re_.set_defaults(func=_cmd_record)

    sh = sub.add_parser("show", help="show recorded collaborations")
    sh.add_argument("--task-id")
    sh.set_defaults(func=_cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
