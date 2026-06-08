#!/usr/bin/env python3
"""eval_harness — agentic 측정 substrate (TASK-238, 15패턴 ①).

라우팅(239)·협업(240)·생성트리오(242)가 **baseline 대비 개선을 증명하는 분모**.
architect subagent(collab-2026-06-05.jsonl): 측정은 peer 아닌 선행 의존 — 없으면 피드백
루프가 merge 시 unfalsifiable(soft 로그 재발).

구성:
  - record_outcome  : per-task outcome/cost 로그(eval_log.jsonl, gitignore = 런타임 데이터).
  - judge_outcome   : **객관 신호**(finish_reason·outcome)로 분류 — LLM-judge 아님(순환 회피).
  - report          : grade/model 별 cost·escalation 집계(스코어보드) + all-Opus baseline.
  - golden set      : 판정 회귀 가드(committed fixture, agents/lead_engineer/eval/golden.jsonl).

"맞는 모델" = escalation 신호 없이 끝낸 가장 싼 tier(별도 judge 모델 불요).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_LOG = ROOT / "eval_log.jsonl"                       # gitignore (런타임)
GOLDEN = ROOT / "agents" / "lead_engineer" / "eval" / "golden.jsonl"  # committed fixture

# 객관 escalation 신호(model 이 약했거나 task 가 컸다 — under-route).
# 'length' 는 ambiguous(성공한 긴 출력일 수 있음) → outcome 도 나쁠 때만 escalate(reviewer #1).
ESCALATION_FINISH = {"error", "cap", "cap-hit", "max_tokens"}
ESCALATION_OUTCOME = {"rejected", "needs-changes", "gate-error", "recurrence", "reopen"}
NEUTRAL_OUTCOME = {"ok", "completed", ""}
MODEL_TIER = {"haiku": 1, "sonnet": 2, "opus": 3}       # 싼→비싼 (TASK-239 over-route 판정용)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------- logger ----------

def record_outcome(task_id: str, grade: str, model: str, tokens: int,
                   finish_reason: str = "stop", outcome: str | None = None,
                   path: Path = EVAL_LOG, policy_model: str | None = None,
                   selected_model: str | None = None,
                   routing_signals: list[str] | None = None,
                   baseline_tokens: int | None = None,
                   actual_tokens_known: bool | None = None,
                   baseline_verdict: str | None = None,
                   collab_verdict: str | None = None,
                   collab_members: list[str] | None = None) -> dict:
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),  # tz-aware(reviewer #3)
           "task_id": task_id, "grade": grade,
           "model": model, "tokens": int(tokens), "finish_reason": finish_reason,
           "outcome": "ok" if outcome is None else outcome}  # None 만 ok(빈 문자열 보존, reviewer #2)
    if policy_model is not None:
        rec["policy_model"] = policy_model
    if selected_model is not None:
        rec["selected_model"] = selected_model
    if routing_signals is not None:
        rec["routing_signals"] = list(routing_signals)
    if baseline_tokens is not None:
        rec["baseline_tokens"] = int(baseline_tokens)
    if actual_tokens_known is not None:
        rec["actual_tokens_known"] = bool(actual_tokens_known)
    if baseline_verdict is not None:
        rec["baseline_verdict"] = baseline_verdict
    if collab_verdict is not None:
        rec["collab_verdict"] = collab_verdict
    if collab_members is not None:
        rec["collab_members"] = list(collab_members)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_outcomes(path: Path = EVAL_LOG) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# ---------- objective judge ----------

def judge_outcome(rec: dict) -> str:
    """객관 신호로 under-route 판정: ok | escalate. LLM-judge 아님(순환 회피).

    escalate = 명확한 실패(error/cap/max_tokens) 또는 나쁜 outcome(rejected/needs-changes/
    gate-error/recurrence/reopen). 'length' 는 outcome 도 나쁠 때만(성공한 긴 출력 false-positive
    방지, reviewer #1). over-route(불필요하게 비쌈)는 report 의 opus_by_grade 가 본다.
    """
    finish = str(rec.get("finish_reason", "")).lower()
    outcome = str(rec.get("outcome", "")).lower()
    if finish in ESCALATION_FINISH:
        return "escalate"
    if finish == "length" and outcome not in NEUTRAL_OUTCOME:
        return "escalate"
    if outcome in ESCALATION_OUTCOME:
        return "escalate"
    return "ok"


# ---------- report (scoreboard) ----------

def report(records: list[dict] | None = None) -> dict:
    records = read_outcomes() if records is None else records
    by_grade: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for r in records:
        verdict = judge_outcome(r)
        g = by_grade.setdefault(r.get("grade", "?"), {"count": 0, "tokens": 0, "escalations": 0})
        g["count"] += 1
        g["tokens"] += int(r.get("tokens", 0))
        g["escalations"] += 1 if verdict == "escalate" else 0
        m = by_model.setdefault(r.get("model", "?"), {"count": 0, "tokens": 0, "escalations": 0})
        m["count"] += 1
        m["tokens"] += int(r.get("tokens", 0))
        m["escalations"] += 1 if verdict == "escalate" else 0
    for d in (*by_grade.values(), *by_model.values()):
        d["escalation_rate"] = round(d["escalations"] / d["count"], 3) if d["count"] else 0.0
    # 등급별 opus 비율 — over-route baseline(reviewer #2): TASK-239 가 줄여야 할 숫자.
    # (전체 opus_share 는 "라우팅 전이라 다 opus"와 "정당하게 opus"를 구분 못 함.)
    opus_by_grade: dict[str, dict] = {}
    for r in records:
        g = opus_by_grade.setdefault(r.get("grade", "?"), {"opus": 0, "total": 0})
        g["total"] += 1
        g["opus"] += 1 if "opus" in str(r.get("model", "")).lower() else 0
    for g in opus_by_grade.values():
        g["opus_share"] = round(g["opus"] / g["total"], 3) if g["total"] else 0.0
    total = len(records)
    opus = sum(1 for r in records if "opus" in str(r.get("model", "")).lower())
    delta_records = [
        r for r in records
        if r.get("baseline_tokens") is not None
        and r.get("actual_tokens_known") is not False
        and int(r.get("tokens", 0) or 0) > 0
    ]
    actual_tokens = sum(int(r.get("tokens", 0) or 0) for r in delta_records)
    baseline_tokens = sum(int(r.get("baseline_tokens", 0) or 0) for r in delta_records)
    saved_tokens = baseline_tokens - actual_tokens
    cost_delta = {
        "actual_tokens": actual_tokens,
        "baseline_tokens": baseline_tokens,
        "saved_tokens": saved_tokens,
        "saved_rate": round(saved_tokens / baseline_tokens, 3) if baseline_tokens else 0.0,
    }
    collab_records = [
        r for r in records
        if r.get("baseline_verdict") is not None
        and r.get("collab_verdict") is not None
        and r.get("collab_members")
        and int(r.get("baseline_tokens", 0) or 0) > 0
    ]
    collaboration_tokens = sum(int(r.get("tokens", 0) or 0) for r in collab_records)
    collaboration_baseline_tokens = sum(int(r.get("baseline_tokens", 0) or 0) for r in collab_records)
    verdict_changes = sum(
        1 for r in collab_records
        if str(r.get("baseline_verdict")) != str(r.get("collab_verdict"))
    )
    collaboration_delta = {
        "total": len(collab_records),
        "verdict_changes": verdict_changes,
        "verdict_change_rate": round(verdict_changes / len(collab_records), 3) if collab_records else 0.0,
        "baseline_tokens": collaboration_baseline_tokens,
        "collaboration_tokens": collaboration_tokens,
        "token_multiplier": (
            round(collaboration_tokens / collaboration_baseline_tokens, 3)
            if collaboration_baseline_tokens else 0.0
        ),
    }
    return {"total": total, "opus_share": round(opus / total, 3) if total else 0.0,
            "by_grade": by_grade, "by_model": by_model, "opus_by_grade": opus_by_grade,
            "cost_delta": cost_delta, "collaboration_delta": collaboration_delta}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return read_outcomes(path)


# ---------- escalation report (자가개선 제안 — 배치, 사람 ratify) ----------

def escalation_proposals(records: list[dict] | None = None, threshold: float = 0.3) -> list[str]:
    """grade 별 escalation율이 threshold 초과면 라우팅표 상향 제안(자동 적용 X — 사람 ratify)."""
    rep = report(records)
    props = []
    for grade, d in rep["by_grade"].items():
        if d["count"] >= 3 and d["escalation_rate"] > threshold:
            props.append(f"{grade}: escalation {d['escalations']}/{d['count']} "
                         f"({d['escalation_rate']}) > {threshold} → 상위 tier 라우팅 제안(사람 ratify)")
    for model, d in rep["by_model"].items():
        if d["count"] >= 3 and d["escalation_rate"] > threshold:
            props.append(f"{model}: escalation {d['escalations']}/{d['count']} "
                         f"({d['escalation_rate']}) > {threshold} → 모델 tier 재검토 제안(사람 ratify)")
    return props


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="agentic 측정 substrate (TASK-238)")
    ap.add_argument("--record", action="store_true", help="outcome/cost 로그 1건 기록")
    ap.add_argument("--report", action="store_true", help="스코어보드 출력")
    ap.add_argument("--proposals", action="store_true", help="라우팅 상향 제안(사람 ratify)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", type=Path, default=EVAL_LOG, help="eval 로그 경로(default: eval_log.jsonl)")
    ap.add_argument("--task-id")
    ap.add_argument("--grade")
    ap.add_argument("--model")
    ap.add_argument("--tokens", type=int)
    ap.add_argument("--finish-reason", default="stop")
    ap.add_argument("--outcome", default="ok")
    ap.add_argument("--policy-model")
    ap.add_argument("--selected-model")
    ap.add_argument("--routing-signal", action="append", default=[])
    ap.add_argument("--baseline-tokens", type=int)
    ap.add_argument("--actual-tokens-unknown", action="store_true",
                    help="mark tokens=0/unknown so cost_delta excludes this record")
    ap.add_argument("--baseline-verdict")
    ap.add_argument("--collab-verdict")
    ap.add_argument("--collab-member", action="append", default=[])
    args = ap.parse_args(argv)
    if args.record:
        missing = [name for name in ("task_id", "grade", "model", "tokens") if getattr(args, name) in (None, "")]
        if missing:
            ap.error("--record requires " + ", ".join("--" + m.replace("_", "-") for m in missing))
        rec = record_outcome(
            args.task_id,
            args.grade,
            args.model,
            args.tokens,
            finish_reason=args.finish_reason,
            outcome=args.outcome,
            path=args.log,
            policy_model=args.policy_model,
            selected_model=args.selected_model,
            routing_signals=args.routing_signal or None,
            baseline_tokens=args.baseline_tokens,
            actual_tokens_known=False if args.actual_tokens_unknown else None,
            baseline_verdict=args.baseline_verdict,
            collab_verdict=args.collab_verdict,
            collab_members=args.collab_member or None,
        )
        print(json.dumps(rec, ensure_ascii=False, indent=2) if args.json else
              f"[eval] recorded {rec['task_id']} {rec['grade']} {rec['model']} {rec['tokens']} tokens")
        return 0
    if args.proposals:
        props = escalation_proposals()
        print(json.dumps(props, ensure_ascii=False, indent=2) if args.json else
              ("\n".join("  " + p for p in props) or "  (제안 없음)"))
        return 0
    rep = report()
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else
          f"[eval] total={rep['total']} opus_share={rep['opus_share']} "
          f"grades={ {g: d['escalation_rate'] for g, d in rep['by_grade'].items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
