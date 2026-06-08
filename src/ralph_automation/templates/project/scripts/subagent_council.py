#!/usr/bin/env python3
"""Subagent council + consensus (TASK-121).

The apex of the Stage 7-A (Claude subagent) path. Where TASK-116 standardized
*one* subagent dispatch and TASK-119 added *one* question/answer exchange,
this module orchestrates a *council* — 2-3 subagents voting on the same
decision — and computes consensus by one of three algorithms:

    majority   — more approve than reject wins (ties => "tie")
    any_veto   — a reject from a veto role (skeptic/auditor) blocks; else majority
    weighted   — per-role weighted score (implementer carries less weight on
                 its own work; skeptic/auditor carry more)

Council membership reuses the 5 standard roles from subagent_dispatch
(implementer / reviewer / auditor / strategist / skeptic). The adversarial
*skeptic* role is the key addition for "find what could break" — same model,
different priority and bias via prompt.

The actual Agent tool invocations stay in the parent conversation; this
module renders the per-member prompts and, once verdicts are collected,
writes a `consensus` evidence message capturing method / parties / verdicts /
final so the decision is auditable.

CLI:
  python scripts/subagent_council.py prompts \\
      --task-id TASK-121 --members implementer,reviewer,skeptic \\
      --intent "review council module"
  python scripts/subagent_council.py decide \\
      --method majority --verdict reviewer=approve --verdict skeptic=reject
  python scripts/subagent_council.py record \\
      --task-id TASK-121 --method any_veto \\
      --verdict implementer=approve --verdict reviewer=approve \\
      --verdict skeptic=reject
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subagent_dispatch as sd  # noqa: E402
from collab_log import GRADE_POLICY, policy_for_grade  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MESSAGES_INBOX = ROOT / "agents" / "messages" / "inbox"
EVENTS_DIR = ROOT / "agents" / "runtime" / "events"

VOTE_VALUES = {"approve", "reject", "abstain"}
CONSENSUS_METHODS = {"majority", "any_veto", "weighted"}
# Roles whose reject is a hard veto under the any_veto method.
DEFAULT_VETO_ROLES = frozenset({"skeptic", "auditor"})
# Default weights for the weighted method. An implementer reviewing its own
# work counts for less; adversarial/independent roles count for more.
DEFAULT_WEIGHTS = {
    "implementer": 1,
    "reviewer": 2,
    "strategist": 2,
    "auditor": 3,
    "skeptic": 3,
}


@dataclass
class Verdict:
    role: str
    vote: str               # approve / reject / abstain
    summary: str = ""

    def __post_init__(self):
        if self.vote not in VOTE_VALUES:
            raise ValueError(
                f"vote must be one of {sorted(VOTE_VALUES)}, got '{self.vote}'"
            )
        if self.role not in sd.SUBAGENT_ROLES:
            raise ValueError(
                f"role must be one of {sd.list_roles()}, got '{self.role}'"
            )


@dataclass
class CouncilResult:
    method: str
    final: str              # approved / rejected / tie
    rationale: str
    verdicts: list[Verdict] = field(default_factory=list)


@dataclass(frozen=True)
class DefaultCollaborationShape:
    grade: str
    mode: str
    context_tier: str
    model: str
    members: list[str]
    consensus_method: str | None
    fanout_count: int
    synthesize: bool


# ---------- consensus algorithms ----------


def consensus_majority(verdicts: list[Verdict]) -> tuple[str, str]:
    approve = sum(1 for v in verdicts if v.vote == "approve")
    reject = sum(1 for v in verdicts if v.vote == "reject")
    if approve > reject:
        return "approved", f"majority approve ({approve} vs {reject})"
    if reject > approve:
        return "rejected", f"majority reject ({reject} vs {approve})"
    return "tie", f"tie ({approve} approve / {reject} reject)"


def consensus_any_veto(verdicts: list[Verdict],
                       veto_roles: frozenset[str] = DEFAULT_VETO_ROLES,
                       ) -> tuple[str, str]:
    """A reject from a veto role blocks. Otherwise majority of all verdicts.

    Note (TASK-121 skeptic finding #2): a veto role that *abstains* casts no
    veto — abstain means "no opinion", so the decision falls back to the
    majority of the members who did vote. This is intentional; an abstaining
    skeptic/auditor neither blocks nor approves.
    """
    vetoes = [v for v in verdicts if v.role in veto_roles and v.vote == "reject"]
    if vetoes:
        names = ", ".join(v.role for v in vetoes)
        return "rejected", f"veto by {names}"
    # No veto — fall back to majority of all verdicts.
    final, why = consensus_majority(verdicts)
    return final, f"no veto; {why}"


def consensus_weighted(verdicts: list[Verdict],
                       weights: dict[str, int] | None = None,
                       ) -> tuple[str, str]:
    w = weights or DEFAULT_WEIGHTS
    score = 0
    for v in verdicts:
        weight = w.get(v.role, 1)
        if v.vote == "approve":
            score += weight
        elif v.vote == "reject":
            score -= weight
    if score > 0:
        return "approved", f"weighted score {score} > 0"
    if score < 0:
        return "rejected", f"weighted score {score} < 0"
    return "tie", "weighted score 0"


def decide(method: str, verdicts: list[Verdict],
           veto_roles: frozenset[str] = DEFAULT_VETO_ROLES,
           weights: dict[str, int] | None = None) -> CouncilResult:
    if method not in CONSENSUS_METHODS:
        raise ValueError(
            f"method must be one of {sorted(CONSENSUS_METHODS)}, got '{method}'"
        )
    if not verdicts:
        raise ValueError("at least one verdict is required")
    # TASK-121 skeptic finding #5 — a role voting twice would be double-counted
    # (or silently inflate a council). Reject duplicate roles outright.
    roles = [v.role for v in verdicts]
    dupes = sorted({r for r in roles if roles.count(r) > 1})
    if dupes:
        raise ValueError(f"duplicate role verdict(s): {', '.join(dupes)}")
    # TASK-121 skeptic finding #1 — a council where every member abstains has
    # reached no decision; surface it as an explicit 'no_quorum' rather than a
    # tie that downstream might read as success.
    if all(v.vote == "abstain" for v in verdicts):
        return CouncilResult(method=method, final="no_quorum",
                             rationale="all members abstained — no decision",
                             verdicts=list(verdicts))
    if method == "majority":
        final, why = consensus_majority(verdicts)
    elif method == "any_veto":
        final, why = consensus_any_veto(verdicts, veto_roles=veto_roles)
    else:
        final, why = consensus_weighted(verdicts, weights=weights)
    return CouncilResult(method=method, final=final, rationale=why,
                         verdicts=list(verdicts))


# ---------- prompt rendering ----------


def render_council_prompts(task_id: str, members: list[str], intent: str,
                           context_packet_path: str | None = None,
                           ) -> dict[str, str]:
    """Render one dispatch prompt per council member (reuses TASK-116)."""
    if len(members) < 2:
        raise ValueError("a council needs at least 2 members")
    if len(set(members)) != len(members):
        # TASK-121 skeptic finding #5 — duplicate members would silently shrink
        # the council (dict keyed by role) or double-weight a perspective.
        raise ValueError(f"duplicate council member(s): {members}")
    for m in members:
        sd.get_role(m)  # validate
    out: dict[str, str] = {}
    for m in members:
        out[m] = sd.render_prompt(
            role_id=m, task_id=task_id, intent=intent,
            context_packet_path=context_packet_path,
        )
    return out


# ---------- TASK-240 default collaboration shape ----------

def _default_consensus_method(members: list[str]) -> str | None:
    if len(members) < 2:
        return None
    if any(member in DEFAULT_VETO_ROLES for member in members):
        return "any_veto"
    return "majority"


def default_collaboration_shape(grade: str) -> DefaultCollaborationShape:
    policy = policy_for_grade(grade)
    members = list(policy.get("subagents") or [])
    for member in members:
        sd.get_role(member)
    return DefaultCollaborationShape(
        grade=grade if grade in GRADE_POLICY else "Medium",
        mode=str(policy.get("mode", "review")),
        context_tier=str(policy.get("tier", "T0")),
        model=str(policy.get("model", "")),
        members=members,
        consensus_method=_default_consensus_method(members),
        fanout_count=len(members),
        synthesize=bool(members),
    )


def default_synthesis_instruction(shape: DefaultCollaborationShape) -> str:
    if not shape.members:
        return "Parent synthesize: no subagent fan-out required; self-review and record waiver."
    if shape.consensus_method:
        return (
            "Parent synthesize: dispatch all prompts in parallel, collect each VERDICT, "
            f"then synthesize with subagent_council decide/record method={shape.consensus_method}. "
            "Treat skeptic/auditor reject as a blocking veto when present."
        )
    return (
        "Parent synthesize: dispatch the reviewer prompt, compare reviewer findings "
        "against self-review, then record collaboration evidence before closure."
    )


def render_default_collaboration_packet(
    task_id: str,
    grade: str,
    intent: str,
    context_packet_path: str | None = None,
) -> dict:
    shape = default_collaboration_shape(grade)
    if len(shape.members) >= 2:
        prompts = render_council_prompts(
            task_id,
            shape.members,
            intent,
            context_packet_path=context_packet_path,
        )
    else:
        prompts = {
            member: sd.render_prompt(
                role_id=member,
                task_id=task_id,
                intent=intent,
                context_packet_path=context_packet_path,
            )
            for member in shape.members
        }
    return {
        "shape": shape,
        "prompts": prompts,
        "synthesis_instruction": default_synthesis_instruction(shape),
    }


# ---------- consensus evidence message ----------


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _new_msg_id(when: _dt.datetime | None = None) -> str:
    w = when or _dt.datetime.now()
    return f"MSG-{w.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def emit_consensus_message(task_id: str, result: CouncilResult,
                           sender: str = "lead-engineer",
                           dry_run: bool = False) -> Path:
    """Write a type=consensus message capturing the council decision.

    Extra (beyond the 9 required) frontmatter fields record the council:
    consensus_method / parties / verdicts / final. check_messages.py treats
    type=consensus like a terminal record (no in_reply_to required).
    """
    msg_id = _new_msg_id()
    parties = ",".join(v.role for v in result.verdicts)
    verdict_lines = [f"{v.role}={v.vote}" for v in result.verdicts]
    front = [
        "---",
        f"id: {msg_id}",
        f"from: {sender}",
        "to: ceo",
        f"task_id: {task_id}",
        f"intent: council consensus ({result.method}) -> {result.final}",
        "type: consensus",
        "status: answered",
        f"ts: {_now_iso()}",
        "in_reply_to:",
        f"consensus_method: {result.method}",
        f"parties: [{parties}]",
        "verdicts:",
    ]
    for line in verdict_lines:
        front.append(f"  - {line}")
    front.append(f"final: {result.final}")
    front.append("evidence: []")
    front.append("next: []")
    front.append("---")
    body_lines = [
        "",
        f"## Council consensus — {result.method}",
        "",
        f"Final: **{result.final}** — {result.rationale}",
        "",
        "| role | vote | summary |",
        "|------|------|---------|",
    ]
    for v in result.verdicts:
        body_lines.append(f"| {v.role} | {v.vote} | {v.summary or '-'} |")
    body_lines.append("")
    text = "\n".join(front) + "\n".join(body_lines) + "\n"
    target = MESSAGES_INBOX / f"{msg_id}.md"
    if not dry_run:
        MESSAGES_INBOX.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return target


# ---------- CLI ----------


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _parse_verdicts(items: list[str]) -> list[Verdict]:
    """Parse --verdict role=vote[:summary] occurrences."""
    out: list[Verdict] = []
    for raw in items or []:
        if "=" not in raw:
            raise ValueError(f"--verdict must be role=vote, got '{raw}'")
        role, _, rest = raw.partition("=")
        vote, _, summary = rest.partition(":")
        out.append(Verdict(role=role.strip(), vote=vote.strip(),
                           summary=summary.strip()))
    return out


def _cmd_prompts(args: argparse.Namespace) -> int:
    members = [m.strip() for m in args.members.split(",") if m.strip()]
    try:
        prompts = render_council_prompts(
            args.task_id, members, args.intent,
            context_packet_path=args.context_packet,
        )
    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for role, prompt in prompts.items():
        print(f"===== council member: {role} =====")
        print(prompt)
        print()
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    try:
        verdicts = _parse_verdicts(args.verdict)
        result = decide(args.method, verdicts)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"method={result.method} final={result.final} — {result.rationale}")
    for v in result.verdicts:
        print(f"  {v.role}: {v.vote}")
    # TASK-121 skeptic finding #1 — only an explicit 'approved' is success.
    # rejected / tie / no_quorum are all non-success (undecided != green-light).
    return 0 if result.final == "approved" else 1


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        verdicts = _parse_verdicts(args.verdict)
        result = decide(args.method, verdicts)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    path = emit_consensus_message(args.task_id, result, sender=args.sender,
            dry_run=args.dry_run)
    word = "would write" if args.dry_run else "wrote"
    print(f"method={result.method} final={result.final} — {result.rationale}")
    print(f"[council] {word} {_display(path)}")
    return 0


def _cmd_default_prompts(args: argparse.Namespace) -> int:
    try:
        packet = render_default_collaboration_packet(
            args.task_id,
            args.grade,
            args.intent,
            context_packet_path=args.context_packet,
        )
    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    shape = packet["shape"]
    members = ", ".join(shape.members) or "(none)"
    method = shape.consensus_method or "single-review"
    print(
        f"default collaboration: {shape.grade} mode={shape.mode} "
        f"tier={shape.context_tier} model={shape.model} members={members} "
        f"synthesis={method}"
    )
    for role, prompt in packet["prompts"].items():
        print(f"===== council member: {role} =====")
        print(prompt)
        print()
    print("===== parent synthesize =====")
    print(packet["synthesis_instruction"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subagent_council.py",
        description="Subagent council + consensus (TASK-121).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("prompts", help="render per-member council prompts")
    pr.add_argument("--task-id", required=True)
    pr.add_argument("--members", required=True,
                    help="comma list, e.g. implementer,reviewer,skeptic")
    pr.add_argument("--intent", required=True)
    pr.add_argument("--context-packet")
    pr.set_defaults(func=_cmd_prompts)

    dp = sub.add_parser("default-prompts", help="render grade-default fan-out prompts")
    dp.add_argument("--task-id", required=True)
    dp.add_argument("--grade", required=True,
                    choices=["Critical", "High", "Medium", "Low"])
    dp.add_argument("--intent", required=True)
    dp.add_argument("--context-packet")
    dp.set_defaults(func=_cmd_default_prompts)

    de = sub.add_parser("decide", help="compute consensus from verdicts (no write)")
    de.add_argument("--method", required=True, choices=sorted(CONSENSUS_METHODS))
    de.add_argument("--verdict", action="append", required=True,
                    help="role=vote[:summary], repeatable")
    de.set_defaults(func=_cmd_decide)

    re_ = sub.add_parser("record", help="compute consensus + write evidence message")
    re_.add_argument("--task-id", required=True)
    re_.add_argument("--method", required=True, choices=sorted(CONSENSUS_METHODS))
    re_.add_argument("--verdict", action="append", required=True,
                     help="role=vote[:summary], repeatable")
    re_.add_argument("--sender", default="lead-engineer")
    re_.add_argument("--dry-run", action="store_true")
    re_.set_defaults(func=_cmd_record)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
