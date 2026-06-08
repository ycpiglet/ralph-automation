"""TASK-134 — /seminar 블라인드 Delphi 합의 단위 시험."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_help_lists_subcommands():
    r = subprocess.run(
        [sys.executable, "scripts/agent_seminar.py", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "seminar" in r.stdout.lower()
    assert "run" in r.stdout.lower()


def test_cli_run_help_shows_tier_flag():
    r = subprocess.run(
        [sys.executable, "scripts/agent_seminar.py", "run", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--tier" in r.stdout
    assert "T2" in r.stdout and "T3" in r.stdout


# --- T2 블라인드 격리 (isolate_round1) ------------------------------------

def _import_isolate():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import isolate_round1  # noqa: WPS433

    return isolate_round1


def test_isolate_round1_each_participant_sees_only_topic():
    isolate_round1 = _import_isolate()
    contexts = isolate_round1(
        topic="propose narrowing backend role",
        participants=["lead-engineer", "ceo", "independent-auditor", "skeptic"],
    )
    for role, ctx in contexts.items():
        assert "propose narrowing backend role" in ctx
        for other in contexts:
            if other != role:
                assert other not in ctx, f"{role} ctx leaks {other}"


def test_isolate_round1_returns_one_context_per_participant():
    isolate_round1 = _import_isolate()
    contexts = isolate_round1(
        topic="x",
        participants=["a", "b", "c"],
    )
    assert set(contexts.keys()) == {"a", "b", "c"}
    # 각 컨텍스트는 string이고 동일한 base 문구를 포함
    for v in contexts.values():
        assert isinstance(v, str)
        assert "do not" in v.lower() or "독립" in v


# --- T3 check_messages TYPE_ENUM 확장 -------------------------------------

def test_check_messages_accepts_seminar_types():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_messages import TYPE_ENUM  # noqa: WPS433

    for t in ("seminar_submission", "seminar_aggregate", "seminar_revision"):
        assert t in TYPE_ENUM, f"{t} must be in TYPE_ENUM"
    # 기존 consensus(verdict) 재사용 확인
    assert "consensus" in TYPE_ENUM


# --- T4 메시지 emit (표준 스키마) ----------------------------------------

def _import_emit():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import (  # noqa: WPS433
        emit_aggregate,
        emit_revision,
        emit_submission,
    )

    return emit_submission, emit_aggregate, emit_revision


def test_emit_submission_blind_marker(tmp_path):
    emit_submission, _, _ = _import_emit()
    p = emit_submission(
        seminar_id="SEMINAR-2026-05-28-001",
        role="lead-engineer", opinion="approve with caveats",
        topic="narrow backend role", inbox_dir=tmp_path / "inbox",
    )
    text = p.read_text(encoding="utf-8")
    assert "type: seminar_submission" in text
    assert "blind: true" in text
    assert "from: lead-engineer" in text
    assert "approve with caveats" in text
    # 표준 스키마
    for field in ("id:", "to:", "intent:", "status: open", "ts:"):
        assert field in text


def test_emit_aggregate_anonymizes(tmp_path):
    _, emit_aggregate, _ = _import_emit()
    p = emit_aggregate(
        seminar_id="SEMINAR-2026-05-28-001",
        aggregated_summary="3 approve, 1 reject with veto. Outlier: anchoring.",
        inbox_dir=tmp_path / "inbox",
    )
    text = p.read_text(encoding="utf-8")
    assert "type: seminar_aggregate" in text
    assert "from: managing-partner" in text
    # 신원 누출 금지
    for leak in ("lead-engineer:", "ceo:", "independent-auditor:"):
        assert leak not in text


def test_emit_revision_links_seminar(tmp_path):
    _, _, emit_revision = _import_emit()
    p = emit_revision(
        seminar_id="SEMINAR-2026-05-28-001",
        role="ceo", revised_opinion="revised after aggregate",
        inbox_dir=tmp_path / "inbox",
    )
    text = p.read_text(encoding="utf-8")
    assert "type: seminar_revision" in text
    assert "seminar_id: SEMINAR-2026-05-28-001" in text
    assert "from: ceo" in text


# --- T5 익명화 + beyond-majority 집계 ------------------------------------

def _import_anonymize_aggregate():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import (  # noqa: WPS433
        aggregate_beyond_majority,
        anonymize_submissions,
    )

    return anonymize_submissions, aggregate_beyond_majority


def test_anonymize_strips_role_attribution():
    anonymize_submissions, _ = _import_anonymize_aggregate()
    subs = [
        {"role": "lead-engineer", "opinion": "approve"},
        {"role": "ceo", "opinion": "approve with caveat — ceo position"},
        {"role": "independent-auditor", "opinion": "reject — evidence missing"},
        {"role": "skeptic", "opinion": "reject — anchoring risk"},
    ]
    anon = anonymize_submissions(subs)
    for a in anon:
        assert "role" not in a, "신원이 새어 나옴"
        # 본문에서도 역할명 마스킹
        op = a.get("opinion", "")
        assert "lead-engineer" not in op
        assert "ceo" not in op
        assert "independent-auditor" not in op
    # 의미는 보존
    assert any("approve" in a["opinion"] for a in anon)
    assert any("anchoring" in a["opinion"] for a in anon)


def test_aggregate_beyond_majority_preserves_outliers():
    _, aggregate_beyond_majority = _import_anonymize_aggregate()
    anon = [
        {"opinion": "approve"},
        {"opinion": "approve"},
        {"opinion": "approve"},
        {"opinion": "reject — concrete reason X"},
    ]
    summary = aggregate_beyond_majority(anon)
    # 다수(approve)도 보이고 소수 reject 의 핵심도 보존
    assert "approve" in summary
    assert "reject" in summary or "X" in summary


# --- T6 정족수 + any_veto + dissent --------------------------------------

def _import_apply_quorum():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import (  # noqa: WPS433
        SeminarVerdict,
        apply_quorum_and_veto,
    )

    return apply_quorum_and_veto, SeminarVerdict


def test_apply_quorum_failure_blocks_decision():
    apply_quorum_and_veto, _ = _import_apply_quorum()
    out = apply_quorum_and_veto(
        submissions=[{"role": "ceo", "vote": "approve", "rationale": "..."}],
        required_quorum=3,
        dissent_record=[],
    )
    assert out.decided is False
    assert "quorum" in out.reason.lower()


def test_apply_veto_from_skeptic_blocks():
    apply_quorum_and_veto, _ = _import_apply_quorum()
    out = apply_quorum_and_veto(
        submissions=[
            {"role": "lead-engineer", "vote": "approve", "rationale": "..."},
            {"role": "ceo", "vote": "approve", "rationale": "..."},
            {"role": "independent-auditor", "vote": "approve", "rationale": "..."},
            {"role": "skeptic", "vote": "veto", "rationale": "anchoring risk"},
        ],
        required_quorum=3,
        dissent_record=[],
    )
    assert out.decided is True
    assert out.verdict == "rejected"
    assert any("anchoring" in d.get("rationale", "") for d in out.dissent)


def test_apply_all_approve_succeeds():
    apply_quorum_and_veto, _ = _import_apply_quorum()
    out = apply_quorum_and_veto(
        submissions=[
            {"role": "lead-engineer", "vote": "approve", "rationale": "..."},
            {"role": "ceo", "vote": "approve", "rationale": "..."},
            {"role": "independent-auditor", "vote": "approve", "rationale": "..."},
            {"role": "skeptic", "vote": "approve", "rationale": "no objection"},
        ],
        required_quorum=3,
        dissent_record=[],
    )
    assert out.verdict == "approved"
    assert out.dissent == []


# --- T7 ensembling baseline 비교 훅 ---------------------------------------

def _import_baseline():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import compute_baseline_majority  # noqa: WPS433

    return compute_baseline_majority


def test_baseline_majority_counts_votes_independently():
    compute_baseline_majority = _import_baseline()
    subs = [
        {"vote": "approve"}, {"vote": "approve"},
        {"vote": "reject"}, {"vote": "veto"},
    ]
    result = compute_baseline_majority(subs)
    assert result["majority"] == "approve"
    assert result["counts"] == {"approve": 2, "reject": 1, "veto": 1}
    assert result["has_veto"] is True


def test_baseline_handles_empty():
    compute_baseline_majority = _import_baseline()
    out = compute_baseline_majority([])
    assert out["majority"] == ""
    assert out["counts"] == {}
    assert out["has_veto"] is False


# --- T8 SEMINAR transcript -----------------------------------------------

def _import_write_transcript():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import write_seminar_transcript  # noqa: WPS433

    return write_seminar_transcript


def test_write_seminar_transcript_has_required_sections(tmp_path):
    write_seminar_transcript = _import_write_transcript()
    p = write_seminar_transcript(
        seminars_dir=tmp_path / "seminars",
        seminar_id="SEMINAR-2026-05-28-001",
        topic="narrow backend role",
        tier="T2",
        round1_anonymized="[anonymized R1]",
        aggregate_summary="[aggregate]",
        round2_anonymized="[anonymized R2]",
        verdict_text="approved",
        baseline="approve (2/4) with veto present",
        dissent=[{"rationale": "anchoring risk"}],
    )
    assert p.exists()
    assert p.name == "SEMINAR-2026-05-28-001.md"
    txt = p.read_text(encoding="utf-8")
    for sec in ("## Topic", "## Round 1 (Anonymized)", "## MP Aggregate",
                "## Round 2 (Anonymized)", "## Verdict",
                "## Baseline (no-deliberation)", "## Dissent"):
        assert sec in txt, f"missing section {sec}"
    # anchoring risk가 dissent 섹션에 보존
    assert "anchoring risk" in txt


# --- T9 run_seminar 오케스트레이션 ----------------------------------------

def _import_run_seminar():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import run_seminar  # noqa: WPS433

    return run_seminar


def test_run_seminar_with_mock_dispatcher_produces_verdict(tmp_path):
    """mock dispatcher가 Round 1/2 응답 — 블라인드→집계→공개→verdict 흐름."""
    run_seminar = _import_run_seminar()

    def fake(*, prompt: str, role: str, round_num: int) -> dict:
        if role == "skeptic":
            return {"vote": "veto", "rationale": "anchoring risk"}
        return {"vote": "approve", "rationale": f"{role}-r{round_num} ok"}

    out = run_seminar(
        seminar_id="SEMINAR-2026-05-28-001",
        topic="narrow backend role",
        tier="T2",
        participants=["lead-engineer", "ceo", "independent-auditor", "skeptic"],
        dispatcher=fake,
        inbox_dir=tmp_path / "inbox",
        seminars_dir=tmp_path / "seminars",
        rounds=2,
        required_quorum=3,
    )
    assert out["verdict"].decided is True
    # skeptic veto → rejected
    assert out["verdict"].verdict == "rejected"
    assert (tmp_path / "seminars" / "SEMINAR-2026-05-28-001.md").exists()
    assert "baseline" in out
    # baseline 계산 결과(독립 다수결)도 노출
    assert out["baseline"]["counts"].get("approve", 0) >= 3


def test_run_seminar_one_round_skips_revision(tmp_path):
    run_seminar = _import_run_seminar()
    calls: list[tuple[str, int]] = []

    def fake(*, prompt: str, role: str, round_num: int) -> dict:
        calls.append((role, round_num))
        return {"vote": "approve", "rationale": "ok"}

    run_seminar(
        seminar_id="SEMINAR-2026-05-28-002",
        topic="x",
        tier="T2",
        participants=["lead-engineer", "ceo", "independent-auditor"],
        dispatcher=fake,
        inbox_dir=tmp_path / "inbox",
        seminars_dir=tmp_path / "seminars",
        rounds=1,
        required_quorum=2,
    )
    # rounds=1이면 dispatcher 호출은 Round 1만 (3회)
    assert all(rn == 1 for _, rn in calls)
    assert len(calls) == 3


# --- T10 verdict 라우팅 (T2→CEO, T3→Owner) -------------------------------

def _import_route_verdict():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import SeminarVerdict, route_verdict  # noqa: WPS433

    return route_verdict, SeminarVerdict


def test_route_verdict_T3_to_owner(tmp_path):
    route_verdict, SeminarVerdict = _import_route_verdict()
    v = SeminarVerdict(decided=True, verdict="approved", reason="all", dissent=[])
    p = route_verdict(
        seminar_id="SEMINAR-2026-05-28-001",
        tier="T3",
        verdict=v,
        inbox_dir=tmp_path / "inbox",
    )
    text = p.read_text(encoding="utf-8")
    assert "audience: Owner" in text
    assert "tier: T3" in text
    assert "type: consensus" in text


def test_route_verdict_T2_to_ceo(tmp_path):
    route_verdict, SeminarVerdict = _import_route_verdict()
    v = SeminarVerdict(decided=True, verdict="rejected", reason="veto", dissent=[])
    p = route_verdict(
        seminar_id="SEMINAR-2026-05-28-001",
        tier="T2",
        verdict=v,
        inbox_dir=tmp_path / "inbox",
    )
    text = p.read_text(encoding="utf-8")
    assert "audience: CEO" in text
    assert "tier: T2" in text


# --- T11 CLI wiring -------------------------------------------------------

def test_cli_run_dry_run_does_not_emit(tmp_path):
    env = os.environ.copy()
    env["AGENT_SEMINAR_REPO_ROOT"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, "scripts/agent_seminar.py", "run",
         "--topic", "test topic", "--tier", "T2", "--dry-run"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    # inbox 디렉토리가 비어 있거나 없어야 함 (dry-run은 메시지 emit 안 함)
    inbox = tmp_path / "agents/messages/inbox"
    msgs = list(inbox.glob("MSG-*.md")) if inbox.exists() else []
    assert len(msgs) == 0


def test_cli_rejects_T0_T1():
    r = subprocess.run(
        [sys.executable, "scripts/agent_seminar.py", "run",
         "--topic", "x", "--tier", "T0", "--dry-run"],
        capture_output=True, text=True,
    )
    # argparse choices=["T2","T3"] 가 T0/T1 거부 → returncode 2
    assert r.returncode == 2


# --- T12 end-to-end (mock dispatcher) -------------------------------------

def test_end_to_end_seminar_with_skeptic_veto(tmp_path):
    """T2 안건: 4 참여자 + skeptic veto → rejected. transcript + audience 라우팅."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_seminar import route_verdict, run_seminar  # noqa: WPS433

    inbox = tmp_path / "inbox"
    seminars = tmp_path / "seminars"

    def mock(*, prompt: str, role: str, round_num: int) -> dict:
        if role == "skeptic":
            return {"vote": "veto", "rationale": "anchoring risk in framing"}
        return {"vote": "approve", "rationale": f"{role} sees value"}

    out = run_seminar(
        seminar_id="SEMINAR-2026-05-28-001",
        topic="narrow backend role",
        tier="T2",
        participants=["lead-engineer", "ceo", "independent-auditor", "skeptic"],
        dispatcher=mock,
        inbox_dir=inbox,
        seminars_dir=seminars,
        rounds=2,
        required_quorum=3,
    )
    assert out["verdict"].verdict == "rejected"
    assert any("anchoring" in d.get("rationale", "") for d in out["verdict"].dissent)
    transcript = seminars / "SEMINAR-2026-05-28-001.md"
    assert transcript.exists()
    # Round 1 anonymized — opinion 본문에 역할명 누출 없는지 확인
    txt = transcript.read_text(encoding="utf-8")
    # opinion 본문 내 role 이름은 마스킹됐어야 함 (transcript 자체에는 모든 정보 노출 가능하니
    # 특히 Round 1 (Anonymized) 섹션 안 본문에서만 검사)
    r1_start = txt.find("## Round 1 (Anonymized)")
    r1_end = txt.find("## MP Aggregate")
    r1_segment = txt[r1_start:r1_end]
    for leak in (" lead-engineer ", " ceo ", " skeptic ", " independent-auditor "):
        # 역할명이 본문에 그대로 노출되지 않아야 함 (anonymized-role 로 치환)
        assert leak not in r1_segment, f"R1 anonymized leaks role: {leak}"

    vp = route_verdict(
        seminar_id="SEMINAR-2026-05-28-001", tier="T2",
        verdict=out["verdict"], inbox_dir=inbox,
    )
    assert "audience: CEO" in vp.read_text(encoding="utf-8")
    # 메시지 수: 4 sub + 1 aggregate + 4 revisions + 1 verdict ≥ 9
    msgs = sorted(inbox.glob("MSG-*.md"))
    assert len(msgs) >= 9
