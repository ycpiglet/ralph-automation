"""TASK-238 — agentic 측정 substrate 테스트."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_eh", ROOT / "scripts" / "eval_harness.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


eh = _load()


# ---- objective judge: golden set 회귀 가드 ----

def test_judge_matches_golden():
    golden = eh.load_golden()
    assert len(golden) >= 10
    for rec in golden:
        assert eh.judge_outcome(rec) == rec["expected"], f"{rec['task_id']} mismatch"


def test_judge_ok_and_escalate():
    assert eh.judge_outcome({"finish_reason": "stop", "outcome": "ok"}) == "ok"
    assert eh.judge_outcome({"finish_reason": "error", "outcome": "ok"}) == "escalate"
    assert eh.judge_outcome({"finish_reason": "stop", "outcome": "rejected"}) == "escalate"


def test_judge_length_is_ambiguous():
    # reviewer #1: 성공한 긴 출력(length+ok)은 escalate 아님, length+나쁜 outcome 만 escalate
    assert eh.judge_outcome({"finish_reason": "length", "outcome": "ok"}) == "ok"
    assert eh.judge_outcome({"finish_reason": "length", "outcome": "needs-changes"}) == "escalate"


def test_report_opus_by_grade_baseline():
    # reviewer #2: 등급별 opus 비율 — 라우팅 전 baseline(routing 이 줄여야 할 숫자)
    recs = [{"grade": "Low", "model": "opus-4-8", "tokens": 1, "finish_reason": "stop", "outcome": "ok"},
            {"grade": "Low", "model": "haiku-4-5", "tokens": 1, "finish_reason": "stop", "outcome": "ok"},
            {"grade": "Critical", "model": "opus-4-8", "tokens": 1, "finish_reason": "stop", "outcome": "ok"}]
    rep = eh.report(recs)
    assert rep["opus_by_grade"]["Low"]["opus_share"] == 0.5
    assert rep["opus_by_grade"]["Critical"]["opus_share"] == 1.0


# ---- logger round-trip ----

def test_record_and_read(tmp_path):
    p = tmp_path / "eval_log.jsonl"
    eh.record_outcome(
        "TASK-X",
        "High",
        "sonnet-4-6",
        40000,
        "stop",
        "ok",
        path=p,
        policy_model="sonnet",
        selected_model="sonnet",
        routing_signals=["grade_policy"],
        baseline_tokens=60000,
    )
    eh.record_outcome("TASK-Y", "Low", "haiku-4-5", 3000, "stop", "ok", path=p)
    recs = eh.read_outcomes(p)
    assert len(recs) == 2 and recs[0]["task_id"] == "TASK-X" and recs[1]["model"] == "haiku-4-5"
    assert recs[0]["policy_model"] == "sonnet"
    assert recs[0]["selected_model"] == "sonnet"
    assert recs[0]["routing_signals"] == ["grade_policy"]
    assert recs[0]["baseline_tokens"] == 60000


def test_cli_record_writes_routing_metadata(tmp_path, capsys):
    p = tmp_path / "eval_log.jsonl"
    rc = eh.main([
        "--record",
        "--task-id", "TASK-X",
        "--grade", "Medium",
        "--model", "sonnet",
        "--tokens", "1200",
        "--policy-model", "sonnet",
        "--selected-model", "sonnet",
        "--routing-signal", "grade_policy",
        "--routing-signal", "prompt_simple_lookup",
        "--baseline-tokens", "3000",
        "--log", str(p),
        "--json",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert '"task_id": "TASK-X"' in captured.out
    rec = eh.read_outcomes(p)[0]
    assert rec["policy_model"] == "sonnet"
    assert rec["selected_model"] == "sonnet"
    assert rec["routing_signals"] == ["grade_policy", "prompt_simple_lookup"]
    assert rec["baseline_tokens"] == 3000


# ---- report (scoreboard) ----

def test_report_aggregates_escalation():
    recs = eh.load_golden()
    rep = eh.report(recs)
    assert rep["total"] == len(recs)
    # High 등급에 escalate 2건(G5·G6) 존재 → escalation_rate > 0
    assert rep["by_grade"]["High"]["escalations"] >= 2
    assert 0.0 <= rep["opus_share"] <= 1.0


def test_report_opus_share():
    recs = [{"grade": "Critical", "model": "opus-4-8", "tokens": 1, "finish_reason": "stop", "outcome": "ok"},
            {"grade": "Low", "model": "haiku-4-5", "tokens": 1, "finish_reason": "stop", "outcome": "ok"}]
    assert eh.report(recs)["opus_share"] == 0.5


def test_report_includes_cost_delta_when_baseline_tokens_present():
    recs = [
        {"grade": "Low", "model": "haiku", "tokens": 100, "baseline_tokens": 400,
         "finish_reason": "stop", "outcome": "ok"},
        {"grade": "High", "model": "sonnet", "tokens": 250, "baseline_tokens": 500,
         "finish_reason": "stop", "outcome": "ok"},
    ]
    delta = eh.report(recs)["cost_delta"]
    assert delta["actual_tokens"] == 350
    assert delta["baseline_tokens"] == 900
    assert delta["saved_tokens"] == 550
    assert delta["saved_rate"] == 0.611


def test_cost_delta_excludes_unknown_zero_actual_tokens():
    recs = [
        {"grade": "Low", "model": "haiku", "tokens": 0, "baseline_tokens": 400,
         "actual_tokens_known": False, "finish_reason": "error", "outcome": "gate-error"},
        {"grade": "High", "model": "sonnet", "tokens": 250, "baseline_tokens": 500,
         "finish_reason": "stop", "outcome": "ok"},
    ]
    delta = eh.report(recs)["cost_delta"]
    assert delta["actual_tokens"] == 250
    assert delta["baseline_tokens"] == 500
    assert delta["saved_tokens"] == 250
    assert delta["saved_rate"] == 0.5


def test_report_includes_collaboration_verdict_delta():
    recs = [
        {"grade": "Medium", "model": "sonnet", "tokens": 1200, "baseline_tokens": 400,
         "baseline_verdict": "approve", "collab_verdict": "approve",
         "collab_members": ["reviewer"]},
        {"grade": "High", "model": "sonnet", "tokens": 1800, "baseline_tokens": 600,
         "baseline_verdict": "approve", "collab_verdict": "reject",
         "collab_members": ["reviewer", "skeptic"]},
    ]
    delta = eh.report(recs)["collaboration_delta"]
    assert delta["total"] == 2
    assert delta["verdict_changes"] == 1
    assert delta["verdict_change_rate"] == 0.5
    assert delta["baseline_tokens"] == 1000
    assert delta["collaboration_tokens"] == 3000
    assert delta["token_multiplier"] == 3.0


def test_collaboration_delta_excludes_unattributed_or_unpriced_rows():
    recs = [
        {"grade": "High", "model": "sonnet", "tokens": 1800, "baseline_tokens": 600,
         "baseline_verdict": "approve", "collab_verdict": "reject"},
        {"grade": "High", "model": "sonnet", "tokens": 1800,
         "baseline_verdict": "approve", "collab_verdict": "reject",
         "collab_members": ["reviewer", "skeptic"]},
        {"grade": "High", "model": "sonnet", "tokens": 1800, "baseline_tokens": 600,
         "baseline_verdict": "approve", "collab_verdict": "reject",
         "collab_members": ["reviewer", "skeptic"]},
    ]
    delta = eh.report(recs)["collaboration_delta"]
    assert delta["total"] == 1
    assert delta["baseline_tokens"] == 600
    assert delta["collaboration_tokens"] == 1800
    assert delta["token_multiplier"] == 3.0


def test_record_outcome_accepts_collaboration_eval_fields(tmp_path):
    rec = eh.record_outcome(
        "TASK-240",
        "High",
        "sonnet",
        1800,
        baseline_tokens=600,
        baseline_verdict="approve",
        collab_verdict="reject",
        collab_members=["reviewer", "skeptic"],
        path=tmp_path / "eval.jsonl",
    )
    assert rec["baseline_verdict"] == "approve"
    assert rec["collab_verdict"] == "reject"
    assert rec["collab_members"] == ["reviewer", "skeptic"]


# ---- escalation proposals (자가개선 — 배치, 사람 ratify) ----

def test_escalation_proposals_fire_over_threshold():
    # High 5건 중 3건 escalate(0.6) > 0.3 → 제안 발생
    recs = [{"grade": "High", "model": "sonnet-4-6", "tokens": 1, "finish_reason": "stop",
             "outcome": ("rejected" if i < 3 else "ok")} for i in range(5)]
    props = eh.escalation_proposals(recs, threshold=0.3)
    assert any("High" in p for p in props)
    assert any("sonnet" in p for p in props)


def test_escalation_proposals_quiet_under_threshold():
    recs = [{"grade": "Low", "model": "haiku-4-5", "tokens": 1, "finish_reason": "stop", "outcome": "ok"}
            for _ in range(5)]
    assert eh.escalation_proposals(recs, threshold=0.3) == []
