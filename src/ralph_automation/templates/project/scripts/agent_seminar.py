"""TASK-134 — /seminar 블라인드 Delphi 합의 (Phase 2).

retro 사다리(TASK-133)의 T2/T3 승인 기구.
독립 무기명 제출 → MP 중립 익명 집계 → 공개·반복 → adversarial skeptic + any_veto.
T2/T3 에서만 호출된다.

설계: docs/specs/2026-05-27-seminar-delphi-council-design.md
근거: agents/research_agent/notes/EVIDENCE-2026-05-28-001-self-improving-agents.md
계획: docs/plans/2026-05-28-seminar-delphi-phase-2.md

CLI:
  python scripts/agent_seminar.py run --topic "..." --tier T2 [--cap N] [--dry-run]
  python scripts/agent_seminar.py run --topic "..." --tier T3 [--rounds 2] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- helpers --------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_msg_id() -> str:
    return (
        f"MSG-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:6]}"
    )


def _write_msg(inbox_dir: Path, mid: str, body: str) -> Path:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{mid}.md"
    path.write_text(body, encoding="utf-8")
    return path


# --- T2 블라인드 격리 ----------------------------------------------------

def isolate_round1(*, topic: str, participants: list[str]) -> dict[str, str]:
    """Round 1: 각 참여자에 동일한 topic만 노출. 서로의 존재·의견은 일절 알리지 않음.

    Delphi method의 핵심 속성. 라운드 1에서 의견은 독립적으로 형성되어야
    echo chamber / information cascade 가 일어나지 않는다.
    """
    base = (
        f"Seminar topic (T2/T3 escalation):\n  {topic}\n\n"
        "Submit your independent opinion. You do not have access to any other "
        "participant's view in this round."
    )
    return {role: base for role in participants}


# --- T4 메시지 emit (표준 message bus 스키마) -----------------------------

def emit_submission(
    *,
    seminar_id: str,
    role: str,
    opinion: str,
    topic: str,
    inbox_dir: Path,
    task_id: str = "TASK-134",
) -> Path:
    """Round 1 블라인드 제출 — `blind: true` 마커 포함."""
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        f"from: {role}\n"
        "to: managing-partner\n"
        f"task_id: {task_id}\n"
        f"intent: seminar submission ({seminar_id})\n"
        "type: seminar_submission\n"
        "status: open\n"
        f"ts: {_now_iso()}\n"
        f"seminar_id: {seminar_id}\n"
        "blind: true\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"Topic: {topic}\n\nOpinion: {opinion}\n"
    )
    return _write_msg(inbox_dir, mid, body)


def emit_aggregate(
    *,
    seminar_id: str,
    aggregated_summary: str,
    inbox_dir: Path,
    task_id: str = "TASK-134",
) -> Path:
    """Managing Partner가 중립 익명 종합본 발행. 신원 일절 노출 금지."""
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        "from: managing-partner\n"
        "to: seminar-participants\n"
        f"task_id: {task_id}\n"
        f"intent: seminar aggregate ({seminar_id})\n"
        "type: seminar_aggregate\n"
        "status: open\n"
        f"ts: {_now_iso()}\n"
        f"seminar_id: {seminar_id}\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"{aggregated_summary}\n"
    )
    return _write_msg(inbox_dir, mid, body)


def emit_revision(
    *,
    seminar_id: str,
    role: str,
    revised_opinion: str,
    inbox_dir: Path,
    task_id: str = "TASK-134",
) -> Path:
    """Round 2: 익명 종합본을 본 뒤 의견 수정 (선택)."""
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        f"from: {role}\n"
        "to: managing-partner\n"
        f"task_id: {task_id}\n"
        f"intent: seminar revision ({seminar_id})\n"
        "type: seminar_revision\n"
        "status: open\n"
        f"ts: {_now_iso()}\n"
        f"seminar_id: {seminar_id}\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"Revised opinion: {revised_opinion}\n"
    )
    return _write_msg(inbox_dir, mid, body)


# --- T5 익명화 + beyond-majority 집계 ------------------------------------

_KNOWN_ROLES = {
    "lead-engineer", "ceo", "owner", "managing-partner", "independent-auditor",
    "doc-steward", "scribe", "research", "timeline",
    "backend", "ci-cd", "uiux", "qa", "beta-tester", "skeptic",
}


def anonymize_submissions(subs: list[dict]) -> list[dict]:
    """제출의 role 신원 제거 + opinion 본문에서 명시 역할명 마스킹.

    의견의 substance는 보존하되 발언자 식별만 차단 (Delphi 익명 원칙).
    """
    out: list[dict] = []
    for sub in subs:
        opinion = sub.get("opinion", "")
        for role in _KNOWN_ROLES:
            opinion = opinion.replace(role, "(anonymized-role)")
        out.append({"opinion": opinion})
    return out


def aggregate_beyond_majority(anonymized: list[dict]) -> str:
    """단순 다수결 금지. 모든 의견을 동등 가시성으로 표면화.

    소수·이상치 의견이 다수와 같은 가시성으로 노출되어 echo chamber 가 합의로
    가는 cascade 를 차단한다.
    """
    if not anonymized:
        return "(no submissions)"
    bullets: list[str] = []
    for sub in anonymized:
        opinion = sub.get("opinion", "").strip()
        if opinion:
            bullets.append(f"- {opinion}")
    return (
        f"Aggregate of {len(bullets)} submissions "
        "(anonymized, equal visibility for outliers):\n"
        + "\n".join(bullets)
    )


# --- T6 정족수 + any_veto + dissent --------------------------------------

@dataclass
class SeminarVerdict:
    """seminar 합의 결과. dissent 명시 보존 (소수 의견 묻지 않음)."""

    decided: bool
    verdict: str   # "approved" | "rejected" | ""
    reason: str
    dissent: list[dict] = field(default_factory=list)


def apply_quorum_and_veto(
    *,
    submissions: list[dict],
    required_quorum: int,
    dissent_record: list[dict],
) -> SeminarVerdict:
    """정족수 미달이면 미결정. skeptic/auditor 가 veto 1건이면 reject.

    `submissions` 각 항목: {role, vote("approve"|"reject"|"veto"), rationale}.
    subagent_council.consensus_any_veto 와 동형 (TASK-121).
    """
    if len(submissions) < required_quorum:
        return SeminarVerdict(
            decided=False,
            verdict="",
            reason=f"quorum unmet ({len(submissions)} < {required_quorum})",
            dissent=list(dissent_record),
        )

    dissent: list[dict] = list(dissent_record)
    for sub in submissions:
        if sub.get("vote") == "veto":
            dissent.append(sub)

    if any(
        sub.get("vote") == "veto"
        and sub.get("role") in {"skeptic", "independent-auditor"}
        for sub in submissions
    ):
        return SeminarVerdict(
            decided=True,
            verdict="rejected",
            reason="veto by skeptic or auditor",
            dissent=dissent,
        )

    approved = sum(1 for s in submissions if s.get("vote") == "approve")
    if approved >= required_quorum:
        return SeminarVerdict(
            decided=True,
            verdict="approved",
            reason=f"{approved}/{len(submissions)} approve, no safety veto",
            dissent=dissent,
        )
    return SeminarVerdict(
        decided=True,
        verdict="rejected",
        reason=f"only {approved}/{len(submissions)} approve",
        dissent=dissent,
    )


# --- T7 ensembling baseline 비교 -----------------------------------------

def compute_baseline_majority(submissions: list[dict]) -> dict:
    """deliberation 없이 Round 1 독립 제출만으로 다수결을 계산.

    Evidence Note caveat — "토론 이득이 단순 ensembling 의 결과일 수 있음".
    seminar 의 deliberation/iteration 단계가 이 baseline 대비 어떤 Δ 를 만들
    수 있는지 측정하기 위한 기준점.
    """
    counts: dict[str, int] = {}
    for sub in submissions:
        vote = sub.get("vote", "")
        if vote:
            counts[vote] = counts.get(vote, 0) + 1
    if not counts:
        return {"majority": "", "counts": {}, "has_veto": False}
    majority = max(counts, key=counts.get)
    return {
        "majority": majority,
        "counts": counts,
        "has_veto": "veto" in counts,
    }


# --- T8 SEMINAR transcript -----------------------------------------------

def write_seminar_transcript(
    *,
    seminars_dir: Path,
    seminar_id: str,
    topic: str,
    tier: str,
    round1_anonymized: str,
    aggregate_summary: str,
    round2_anonymized: str,
    verdict_text: str,
    baseline: str,
    dissent: list[dict],
) -> Path:
    """seminar 한 사이클의 전체 transcript 파일 작성.

    섹션: Topic / Round 1 (Anonymized) / MP Aggregate / Round 2 (Anonymized) /
          Verdict / Baseline (no-deliberation) / Dissent.
    """
    seminars_dir.mkdir(parents=True, exist_ok=True)
    path = seminars_dir / f"{seminar_id}.md"
    if not dissent:
        dissent_lines = "- (none)"
    else:
        dissent_lines = "\n".join(
            f"- {d.get('rationale', '')}" for d in dissent
        )
    body = (
        "---\n"
        "type: seminar\n"
        f"id: {seminar_id}\n"
        f"tier: {tier}\n"
        f"recorded_at: {_now_iso()}\n"
        "---\n\n"
        f"## Topic\n\n{topic}\n\n"
        f"## Round 1 (Anonymized)\n\n{round1_anonymized}\n\n"
        f"## MP Aggregate\n\n{aggregate_summary}\n\n"
        f"## Round 2 (Anonymized)\n\n{round2_anonymized}\n\n"
        f"## Verdict\n\n{verdict_text}\n\n"
        f"## Baseline (no-deliberation)\n\n{baseline}\n\n"
        f"## Dissent\n\n{dissent_lines}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


# --- T9 run_seminar 오케스트레이션 ----------------------------------------

def run_seminar(
    *,
    seminar_id: str,
    topic: str,
    tier: str,
    participants: list[str],
    dispatcher,
    inbox_dir: Path,
    seminars_dir: Path,
    rounds: int = 2,
    required_quorum: int = 3,
) -> dict:
    """블라인드 Delphi 1 사이클: blind R1 → 익명 집계 → R2 reveal → verdict.

    dispatcher 시그니처: (prompt, role, round_num) -> {"vote": ..., "rationale": ...}
    Round 1 컨텍스트는 isolate_round1 로 격리됨. Round 2(선택) 는 aggregate 본 뒤.
    """
    contexts = isolate_round1(topic=topic, participants=participants)
    round1_subs: list[dict] = []
    for role, ctx in contexts.items():
        resp = dispatcher(prompt=ctx, role=role, round_num=1)
        round1_subs.append({"role": role, **resp})
        emit_submission(
            seminar_id=seminar_id,
            role=role,
            opinion=str(resp.get("rationale", "")),
            topic=topic,
            inbox_dir=inbox_dir,
        )

    anon_r1 = anonymize_submissions(round1_subs)
    aggregate = aggregate_beyond_majority(anon_r1)
    emit_aggregate(
        seminar_id=seminar_id,
        aggregated_summary=aggregate,
        inbox_dir=inbox_dir,
    )

    final_subs = round1_subs
    if rounds >= 2:
        revised: list[dict] = []
        for role in participants:
            resp = dispatcher(prompt=aggregate, role=role, round_num=2)
            revised.append({"role": role, **resp})
            emit_revision(
                seminar_id=seminar_id,
                role=role,
                revised_opinion=str(resp.get("rationale", "")),
                inbox_dir=inbox_dir,
            )
        final_subs = revised

    verdict = apply_quorum_and_veto(
        submissions=final_subs,
        required_quorum=required_quorum,
        dissent_record=[],
    )
    baseline = compute_baseline_majority(round1_subs)
    anon_r2 = anonymize_submissions(final_subs)

    write_seminar_transcript(
        seminars_dir=seminars_dir,
        seminar_id=seminar_id,
        topic=topic,
        tier=tier,
        round1_anonymized=str(anon_r1),
        aggregate_summary=aggregate,
        round2_anonymized=str(anon_r2),
        verdict_text=f"{verdict.verdict} — {verdict.reason}",
        baseline=str(baseline),
        dissent=verdict.dissent,
    )

    return {"verdict": verdict, "baseline": baseline, "rounds": rounds}


# --- T10 verdict → tier 라우팅 (T2→CEO, T3→Owner) -------------------------

def route_verdict(
    *,
    seminar_id: str,
    tier: str,
    verdict: SeminarVerdict,
    inbox_dir: Path,
    task_id: str = "TASK-134",
) -> Path:
    """verdict 를 consensus 메시지로 emit, tier 에 따라 audience 라우팅.

    T3 → Owner (사람, 큰 방향/구조 변경)
    T2 → CEO   (자율 에이전트 + Auditor 승인)
    audience 라우팅은 TASK-130 Owner/CEO 모델에 따른다.
    """
    audience = "Owner" if tier == "T3" else "CEO"
    recipient = "owner" if tier == "T3" else "ceo"
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        "from: managing-partner\n"
        f"to: {recipient}\n"
        f"task_id: {task_id}\n"
        f"intent: seminar verdict ({seminar_id})\n"
        "type: consensus\n"
        f"status: {'answered' if verdict.decided else 'open'}\n"
        f"ts: {_now_iso()}\n"
        f"seminar_id: {seminar_id}\n"
        f"tier: {tier}\n"
        f"audience: {audience}\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"Verdict: {verdict.verdict}\n"
        f"Reason: {verdict.reason}\n"
        f"Dissent count: {len(verdict.dissent)}\n"
    )
    return _write_msg(inbox_dir, mid, body)


def _cmd_run(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(
            f"[dry-run] tier={args.tier} topic={args.topic!r} "
            f"cap={args.cap} rounds={args.rounds}"
        )
        return 0
    # 실 모드는 dispatcher 주입을 요구 — 라이브러리 API (run_seminar) 사용.
    print(
        "real-mode dispatcher injection required "
        "(use run_seminar library API with an Agent-tool wrapper)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agent_seminar",
        description="/seminar blind Delphi consensus (Phase 2)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a seminar on a topic")
    run.add_argument("--topic", required=True, help="agenda one-liner")
    run.add_argument(
        "--tier",
        required=True,
        choices=["T2", "T3"],
        help="seminar is only valid for T2/T3 escalations",
    )
    run.add_argument(
        "--cap", type=int, default=6,
        help="max participants (default 6)",
    )
    run.add_argument(
        "--rounds", type=int, default=2,
        help="Delphi rounds (default 2)",
    )
    run.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
