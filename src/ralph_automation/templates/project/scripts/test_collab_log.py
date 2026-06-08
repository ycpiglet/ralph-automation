"""Unit tests for collab_log (TASK-123)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collab_log as cl  # noqa: E402
import model_routing as mr  # noqa: E402


# ---------- grade policy ----------

def test_policy_critical_is_council():
    p = cl.policy_for_grade("Critical")
    assert p["mode"] == "council"
    assert set(p["subagents"]) == {"reviewer", "auditor", "skeptic"}
    assert p["tier"] == "T2"


def test_policy_high_is_review_adversarial():
    p = cl.policy_for_grade("High")
    assert "skeptic" in p["subagents"]
    assert p["tier"] == "T1"


def test_policy_medium_single_reviewer():
    p = cl.policy_for_grade("Medium")
    assert p["subagents"] == ["reviewer"]
    assert p["tier"] == "T0"


def test_policy_low_is_self():
    p = cl.policy_for_grade("Low")
    assert p["mode"] == "self"
    assert p["subagents"] == []


def test_policy_unknown_grade_defaults_medium():
    assert cl.policy_for_grade("Bogus") == cl.policy_for_grade("Medium")


def test_policy_model_tiers_match_model_routing_source():
    for grade, tier in mr.GRADE_POLICY.items():
        assert cl.policy_for_grade(grade)["model"] == tier


# ---------- tier escalation ----------

def test_escalate_on_full_file_signal():
    assert cl.escalate_tier("T0", "this needs full-file verification") == "T3"


def test_escalate_korean_signal():
    assert cl.escalate_tier("T0", "이 부분은 전체 확인 필요") == "T3"


def test_escalate_surrounding_signal():
    assert cl.escalate_tier("T0", "주변 확인이 필요함") == "T1"


def test_no_escalation_when_clean():
    assert cl.escalate_tier("T0", "looks good, no issues") == "T0"


def test_escalate_never_downgrades():
    # already T2, a T1 signal should not lower it
    assert cl.escalate_tier("T2", "주변 확인") == "T2"


# ---------- record / read ----------

def test_record_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    path = cl.record_collaboration(
        task_id="TASK-123", tier="T0", method="reviewer", verdict="approve",
        plan="diff-only", parties=["implementer", "reviewer"], tokens=38000,
        findings=["no issues"], outcome="merged",
    )
    assert path.exists()
    rec = json.loads(path.read_text(encoding="utf-8").strip())
    assert rec["task_id"] == "TASK-123"
    assert rec["tier"] == "T0"
    assert rec["verdict"] == "approve"
    assert rec["tokens"] == 38000
    assert rec["parties"] == ["implementer", "reviewer"]


def test_record_collaboration_writes_eval_when_baseline_present(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path / "events")
    eval_log = tmp_path / "eval.jsonl"
    cl.record_collaboration(
        task_id="TASK-240",
        tier="T1",
        method="reviewer+skeptic",
        verdict="needs-changes",
        parties=["reviewer", "skeptic"],
        tokens=1800,
        baseline_verdict="approve",
        baseline_tokens=600,
        grade="High",
        eval_log_path=eval_log,
    )
    recs = cl.eval_harness.read_outcomes(eval_log)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["task_id"] == "TASK-240"
    assert rec["grade"] == "High"
    assert rec["model"] == "sonnet"
    assert rec["tokens"] == 1800
    assert rec["baseline_tokens"] == 600
    assert rec["baseline_verdict"] == "approve"
    assert rec["collab_verdict"] == "needs-changes"
    assert rec["collab_members"] == ["reviewer", "skeptic"]
    delta = cl.eval_harness.report(recs)["collaboration_delta"]
    assert delta["total"] == 1
    assert delta["token_multiplier"] == 3.0


def test_record_collaboration_skips_eval_without_members_or_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path / "events")
    eval_log = tmp_path / "eval.jsonl"
    cl.record_collaboration(
        task_id="TASK-240",
        tier="T1",
        method="reviewer+skeptic",
        verdict="needs-changes",
        tokens=1800,
        baseline_verdict="approve",
        eval_log_path=eval_log,
    )
    assert not eval_log.exists()


def test_record_dry_run_no_write(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    path = cl.record_collaboration(
        task_id="TASK-123", tier="T0", method="reviewer", verdict="approve",
        dry_run=True,
    )
    assert not path.exists()


def test_record_rejects_bad_tier(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    with pytest.raises(ValueError):
        cl.record_collaboration("TASK-1", "T9", "reviewer", "approve")


def test_record_rejects_bad_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    with pytest.raises(ValueError):
        cl.record_collaboration("TASK-1", "T0", "reviewer", "maybe")


def test_read_filters_by_task(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    cl.record_collaboration("TASK-1", "T0", "reviewer", "approve")
    cl.record_collaboration("TASK-2", "T1", "skeptic", "reject")
    assert len(cl.read_collaborations()) == 2
    assert len(cl.read_collaborations("TASK-1")) == 1
    assert cl.read_collaborations("TASK-1")[0]["task_id"] == "TASK-1"


def test_read_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path / "nope")
    assert cl.read_collaborations() == []


# ---------- CLI ----------

def test_cli_policy(capsys):
    rc = cl.main(["policy", "--grade", "Critical"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "council" in out
    assert "skeptic" in out


def test_cli_record_and_show(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "EVENTS_DIR", tmp_path)
    rc = cl.main(["record", "--task-id", "TASK-9", "--tier", "T0",
                  "--method", "reviewer", "--verdict", "approve",
                  "--tokens", "100"])
    assert rc == 0
    rc2 = cl.main(["show", "--task-id", "TASK-9"])
    assert rc2 == 0
    assert "TASK-9" in capsys.readouterr().out
