"""Unit tests for subagent_council (TASK-121).

Covers:
  - Verdict validation (vote enum, role enum)
  - consensus_majority / any_veto / weighted algorithms
  - decide dispatch + error handling
  - render_council_prompts reuses subagent_dispatch
  - emit_consensus_message writes type=consensus that lints clean
  - CLI: prompts / decide / record
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subagent_council as sc  # noqa: E402
import check_messages as cm  # noqa: E402


def V(role, vote, summary=""):
    return sc.Verdict(role=role, vote=vote, summary=summary)


# ---------- Verdict validation ----------

def test_verdict_rejects_bad_vote():
    with pytest.raises(ValueError):
        sc.Verdict(role="reviewer", vote="maybe")


def test_verdict_rejects_bad_role():
    with pytest.raises(ValueError):
        sc.Verdict(role="nonexistent", vote="approve")


# ---------- majority ----------

def test_majority_approve_wins():
    final, _ = sc.consensus_majority([V("implementer", "approve"),
                                      V("reviewer", "approve"),
                                      V("skeptic", "reject")])
    assert final == "approved"


def test_majority_reject_wins():
    final, _ = sc.consensus_majority([V("implementer", "reject"),
                                      V("reviewer", "reject"),
                                      V("skeptic", "approve")])
    assert final == "rejected"


def test_majority_tie():
    final, _ = sc.consensus_majority([V("reviewer", "approve"),
                                      V("skeptic", "reject")])
    assert final == "tie"


def test_majority_abstain_does_not_count():
    final, _ = sc.consensus_majority([V("reviewer", "approve"),
                                      V("auditor", "abstain"),
                                      V("skeptic", "abstain")])
    assert final == "approved"


# ---------- any_veto ----------

def test_any_veto_skeptic_reject_blocks():
    final, why = sc.consensus_any_veto([V("implementer", "approve"),
                                        V("reviewer", "approve"),
                                        V("skeptic", "reject")])
    assert final == "rejected"
    assert "veto" in why


def test_any_veto_auditor_reject_blocks():
    final, _ = sc.consensus_any_veto([V("reviewer", "approve"),
                                      V("auditor", "reject")])
    assert final == "rejected"


def test_any_veto_non_veto_role_reject_falls_to_majority():
    # reviewer is not a veto role -> majority applies (2 approve vs 1 reject)
    final, _ = sc.consensus_any_veto([V("implementer", "approve"),
                                      V("strategist", "approve"),
                                      V("reviewer", "reject")])
    assert final == "approved"


def test_any_veto_no_veto_passes():
    final, why = sc.consensus_any_veto([V("implementer", "approve"),
                                        V("skeptic", "approve")])
    assert final == "approved"
    assert "no veto" in why


# ---------- weighted ----------

def test_weighted_skeptic_outweighs_implementer():
    # implementer(1) approve vs skeptic(3) reject -> score -2 -> rejected
    final, _ = sc.consensus_weighted([V("implementer", "approve"),
                                      V("skeptic", "reject")])
    assert final == "rejected"


def test_weighted_two_reviewers_outweigh_one_skeptic():
    # reviewer(2)+strategist(2) approve = 4 vs skeptic(3) reject -> +1 approved
    final, _ = sc.consensus_weighted([V("reviewer", "approve"),
                                      V("strategist", "approve"),
                                      V("skeptic", "reject")])
    assert final == "approved"


def test_weighted_custom_weights():
    final, _ = sc.consensus_weighted(
        [V("implementer", "approve"), V("reviewer", "reject")],
        weights={"implementer": 5, "reviewer": 1},
    )
    assert final == "approved"


# ---------- decide ----------

def test_decide_rejects_bad_method():
    with pytest.raises(ValueError):
        sc.decide("nonsense", [V("reviewer", "approve")])


def test_decide_rejects_empty_verdicts():
    with pytest.raises(ValueError):
        sc.decide("majority", [])


def test_decide_returns_council_result():
    r = sc.decide("majority", [V("reviewer", "approve"), V("skeptic", "approve")])
    assert isinstance(r, sc.CouncilResult)
    assert r.final == "approved"
    assert r.method == "majority"
    assert len(r.verdicts) == 2


# ---------- TASK-121 council-review fixes (from live skeptic findings) ----------

def test_decide_all_abstain_is_no_quorum():
    """Finding #1 — an all-abstain council reaches no decision (not a tie)."""
    for method in ("majority", "any_veto", "weighted"):
        r = sc.decide(method, [V("reviewer", "abstain"), V("skeptic", "abstain")])
        assert r.final == "no_quorum", method


def test_decide_rejects_duplicate_roles():
    """Finding #5 — same role voting twice would double-count; reject it."""
    with pytest.raises(ValueError):
        sc.decide("majority", [V("reviewer", "approve"), V("reviewer", "reject")])


def test_any_veto_role_abstain_is_neutral():
    """Finding #2 — an abstaining veto role casts no veto (documented intent)."""
    # skeptic abstains -> no veto -> majority of remaining (implementer approve)
    final, _ = sc.consensus_any_veto([V("implementer", "approve"),
                                      V("skeptic", "abstain")])
    assert final == "approved"
    # but an explicit skeptic reject DOES block
    final2, _ = sc.consensus_any_veto([V("implementer", "approve"),
                                       V("skeptic", "reject")])
    assert final2 == "rejected"


def test_render_council_prompts_rejects_duplicate_members():
    with pytest.raises(ValueError):
        sc.render_council_prompts("TASK-121", ["reviewer", "reviewer"], "x")


def test_cli_decide_tie_is_nonzero(capsys):
    """Finding #1 — a tie must not exit 0 (undecided != success)."""
    rc = sc.main(["decide", "--method", "majority",
                  "--verdict", "reviewer=approve",
                  "--verdict", "skeptic=reject"])
    assert rc == 1
    assert "tie" in capsys.readouterr().out


def test_cli_decide_no_quorum_is_nonzero(capsys):
    rc = sc.main(["decide", "--method", "majority",
                  "--verdict", "reviewer=abstain",
                  "--verdict", "skeptic=abstain"])
    assert rc == 1
    assert "no_quorum" in capsys.readouterr().out


# ---------- render_council_prompts ----------

def test_render_council_prompts_one_per_member():
    prompts = sc.render_council_prompts(
        "TASK-121", ["implementer", "reviewer", "skeptic"], "review module")
    assert set(prompts.keys()) == {"implementer", "reviewer", "skeptic"}
    assert "IMPLEMENTER subagent" in prompts["implementer"]
    assert "SKEPTIC subagent" in prompts["skeptic"]


def test_render_council_prompts_requires_two_members():
    with pytest.raises(ValueError):
        sc.render_council_prompts("TASK-121", ["reviewer"], "x")


# ---------- TASK-240 default collaboration shape ----------

def test_default_collaboration_shape_by_grade():
    medium = sc.default_collaboration_shape("Medium")
    assert medium.members == ["reviewer"]
    assert medium.consensus_method is None
    assert medium.fanout_count == 1
    assert medium.synthesize is True

    high = sc.default_collaboration_shape("High")
    assert high.members == ["reviewer", "skeptic"]
    assert high.consensus_method == "any_veto"
    assert high.fanout_count == 2

    critical = sc.default_collaboration_shape("Critical")
    assert critical.members == ["reviewer", "auditor", "skeptic"]
    assert critical.consensus_method == "any_veto"
    assert critical.fanout_count == 3


def test_render_default_collaboration_packet_reuses_dispatch_prompts():
    packet = sc.render_default_collaboration_packet(
        "TASK-240",
        "High",
        "review default collaboration shape",
    )
    assert packet["shape"].members == ["reviewer", "skeptic"]
    assert set(packet["prompts"]) == {"reviewer", "skeptic"}
    assert "REVIEWER subagent" in packet["prompts"]["reviewer"]
    assert "SKEPTIC subagent" in packet["prompts"]["skeptic"]
    assert "parent synthesize" in packet["synthesis_instruction"].lower()
    assert "any_veto" in packet["synthesis_instruction"]


def test_cli_default_prompts_outputs_parent_synthesis(capsys):
    rc = sc.main([
        "default-prompts",
        "--task-id", "TASK-240",
        "--grade", "High",
        "--intent", "review default collaboration shape",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "default collaboration: High" in out
    assert "council member: reviewer" in out
    assert "council member: skeptic" in out
    assert "parent synthesize" in out.lower()


# ---------- emit_consensus_message ----------

def test_emit_consensus_message_lints_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MESSAGES_INBOX", tmp_path)
    monkeypatch.setattr(cm, "MESSAGES_DIR", tmp_path.parent)
    # write to an "inbox" subdir so check_messages scans it
    inbox = tmp_path.parent / "inbox"
    monkeypatch.setattr(sc, "MESSAGES_INBOX", inbox)
    r = sc.decide("any_veto", [V("implementer", "approve"),
                               V("reviewer", "approve"),
                               V("skeptic", "reject")])
    path = sc.emit_consensus_message("TASK-121", r)
    assert path.exists()
    meta, err = cm.load_frontmatter(path)
    assert err == "" and meta is not None
    for fld in cm.REQUIRED_FIELDS:
        assert fld in meta
    assert meta["type"] == "consensus"
    assert meta["consensus_method"] == "any_veto"
    assert meta["final"] == "rejected"
    assert "implementer=approve" in meta["verdicts"]


def test_emit_consensus_message_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MESSAGES_INBOX", tmp_path)
    r = sc.decide("majority", [V("reviewer", "approve"), V("skeptic", "approve")])
    path = sc.emit_consensus_message("TASK-121", r, dry_run=True)
    assert not path.exists()


# ---------- consensus message passes the real lint ----------

def test_consensus_message_passes_check_messages_lint(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(sc, "MESSAGES_INBOX", tmp_path / "inbox")
    r = sc.decide("weighted", [V("reviewer", "approve"),
                               V("strategist", "approve"),
                               V("skeptic", "reject")])
    sc.emit_consensus_message("TASK-121", r)
    errors, _ = cm.lint()
    assert errors == 0


# ---------- CLI ----------

def test_cli_decide_veto_exit_code(capsys):
    rc = sc.main(["decide", "--method", "any_veto",
                  "--verdict", "reviewer=approve",
                  "--verdict", "skeptic=reject"])
    assert rc == 1   # rejected -> nonzero
    out = capsys.readouterr().out
    assert "rejected" in out


def test_cli_decide_approve_exit_code(capsys):
    rc = sc.main(["decide", "--method", "majority",
                  "--verdict", "reviewer=approve",
                  "--verdict", "strategist=approve"])
    assert rc == 0
    assert "approved" in capsys.readouterr().out


def test_cli_prompts(capsys):
    rc = sc.main(["prompts", "--task-id", "TASK-121",
                  "--members", "implementer,skeptic",
                  "--intent", "council probe"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "council member: implementer" in out
    assert "council member: skeptic" in out


def test_cli_record_dry_run(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MESSAGES_INBOX", tmp_path)
    rc = sc.main(["record", "--task-id", "TASK-121", "--method", "majority",
                  "--verdict", "reviewer=approve", "--verdict", "skeptic=approve",
                  "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would write" in out


def test_cli_verdict_bad_format(capsys):
    rc = sc.main(["decide", "--method", "majority", "--verdict", "noequalsign"])
    assert rc == 2
