#!/usr/bin/env python3
"""Session Budget estimator (TASK-093).

Implements AGENTS.md §14 — Session Budget Protocol — at the CLI level so
either a human or a LLM can sanity-check the "proceed / partial / stop /
handoff" decision before starting a task.

Usage:
  python scripts/budget_estimate.py --priority High --remaining 50K
  python scripts/budget_estimate.py --task TASK-095 --remaining 12%
  python scripts/budget_estimate.py --kind logging-only --remaining 30%
  python scripts/budget_estimate.py --catalog        # print baseline cost table
  python scripts/budget_estimate.py --task TASK-095 --remaining 25K --format json

Inputs:
  --priority  Medium | High | Critical                (manual classification)
  --kind      logging-only | handoff-only             (special cases)
  --task      TASK-NNN                                 (look up frontmatter `priority`)
  --remaining K-suffix value (e.g. 50K) or % of 1M context (e.g. 12%)

Output: one-line `[budget]` summary matching AGENTS.md §14.3 + an
optional JSON payload.

Baseline costs come from agents/lead_engineer/TOKEN-BUDGET.md and are
intentionally conservative (the avg of the published ranges).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"

# Baseline averages (mid-range from TOKEN-BUDGET.md). Update when the
# catalog is updated — also bumped by check_agent_docs.check_token_budget
# if it ages past 6 months.
BASELINE_COST = {
    "medium":       25_000,
    "high":         40_000,
    "critical":     55_000,
    "logging-only": 15_000,
    "handoff-only": 10_000,
}
RESERVE = 10_000               # AGENTS.md §14.3
CONTEXT_WINDOW_DEFAULT = 1_000_000   # Claude Opus 4.7 1M

DECISIONS = ["진행", "부분 진행", "시작 안 함", "즉시 Handoff"]


def parse_remaining(s: str, context: int = CONTEXT_WINDOW_DEFAULT) -> int:
    """Parse '50K' / '12%' / '12000' into an absolute token count."""
    s = s.strip().lower()
    if s.endswith("%"):
        pct = float(s[:-1])
        return int(context * pct / 100)
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


def normalize_kind(s: str) -> str:
    k = s.strip().lower()
    if k in {"medium", "med", "m"}:
        return "medium"
    if k in {"high", "h"}:
        return "high"
    if k in {"critical", "crit", "c"}:
        return "critical"
    if k in {"logging-only", "logging", "log", "meta"}:
        return "logging-only"
    if k in {"handoff-only", "handoff", "ho"}:
        return "handoff-only"
    raise SystemExit(f"unknown priority/kind '{s}'. accept: {sorted(BASELINE_COST)}")


def load_task_priority(task_id: str) -> str:
    """Look up frontmatter priority from a TASK file."""
    candidates = sorted(TASKS_DIR.glob(f"{task_id}-*.md"))
    if not candidates:
        direct = TASKS_DIR / f"{task_id}.md"
        if direct.exists():
            candidates = [direct]
    if not candidates:
        raise SystemExit(f"TASK file not found for {task_id}")
    text = candidates[0].read_text(encoding="utf-8")
    m = re.search(r"(?m)^priority:\s*([A-Za-z]+)\s*$", text)
    if not m:
        raise SystemExit(f"{candidates[0].name}: frontmatter priority not found")
    return m.group(1)


def decide(remaining: int, kind: str) -> dict:
    """AGENTS.md §14.3 의사코드 그대로."""
    expected = BASELINE_COST[kind]
    full_threshold    = expected + int(RESERVE * 1.5)
    partial_threshold = int(expected * 0.6) + RESERVE
    stop_threshold    = RESERVE * 2

    if remaining >= full_threshold:
        decision = "진행"
    elif remaining >= partial_threshold:
        decision = "부분 진행"
    elif remaining >= stop_threshold:
        decision = "시작 안 함"
    else:
        decision = "즉시 Handoff"

    return {
        "remaining": remaining,
        "kind": kind,
        "expected_baseline": expected,
        "reserve": RESERVE,
        "thresholds": {
            "full":    full_threshold,
            "partial": partial_threshold,
            "stop":    stop_threshold,
        },
        "decision": decision,
    }


def _fmt_k(n: int) -> str:
    if n >= 1000:
        v = n / 1000
        return f"{v:.0f}K" if v == int(v) else f"{v:.1f}K"
    return str(n)


def render_line(result: dict) -> str:
    return (
        f"[budget] 잔량 {_fmt_k(result['remaining'])} / "
        f"분류 {result['kind']} 예상 ~{_fmt_k(result['expected_baseline'])} / "
        f"Reserve {_fmt_k(result['reserve'])} → {result['decision']}"
    )


def print_catalog() -> None:
    print("baseline mid-range costs (avg of TOKEN-BUDGET.md published ranges):")
    for k, v in BASELINE_COST.items():
        print(f"  {k:<14}  ~{_fmt_k(v)}")
    print(f"  reserve         {_fmt_k(RESERVE)}")
    print(f"  context window  {_fmt_k(CONTEXT_WINDOW_DEFAULT)} (Claude Opus 4.7 1M, override via --context)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate session budget decision (AGENTS.md §14).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--priority", help="Medium / High / Critical")
    parser.add_argument("--kind", help="logging-only / handoff-only (override priority)")
    parser.add_argument("--task", help="TASK-NNN (look up frontmatter priority)")
    parser.add_argument("--remaining", help="remaining tokens (e.g. 50K, 12%, 25000)")
    parser.add_argument("--context", type=int, default=CONTEXT_WINDOW_DEFAULT,
                        help="context window (default 1000000)")
    parser.add_argument("--format", choices=["line", "json"], default="line")
    parser.add_argument("--catalog", action="store_true",
                        help="print baseline cost table and exit")
    args = parser.parse_args()

    if args.catalog:
        print_catalog()
        return 0

    if not args.remaining:
        parser.error("--remaining is required (or use --catalog)")
        return 2

    if sum(bool(x) for x in (args.priority, args.kind, args.task)) != 1:
        parser.error("exactly one of --priority / --kind / --task is required")
        return 2

    if args.priority:
        kind = normalize_kind(args.priority)
    elif args.kind:
        kind = normalize_kind(args.kind)
    else:
        kind = normalize_kind(load_task_priority(args.task))

    remaining = parse_remaining(args.remaining, context=args.context)
    result = decide(remaining, kind)

    if args.format == "json":
        if args.task:
            result["task"] = args.task
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
