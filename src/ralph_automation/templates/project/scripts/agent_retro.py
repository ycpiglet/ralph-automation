"""TASK-133 — /retro 자기개선 루프 + 계층 에스컬레이션 (Phase 1).

각 에이전트가 자기 작업을 회고하고, 결과의 중요도(티어 T0~T3)에 따라 SKILL.md를
자율 적용 또는 상위 계층 에스컬레이션한다.

설계: docs/specs/2026-05-27-retro-self-improvement-design.md
근거: agents/research_agent/notes/EVIDENCE-2026-05-28-001-self-improving-agents.md
계획: docs/plans/2026-05-28-retro-self-improvement-phase-1.md

CLI:
  python scripts/agent_retro.py run <role> [--dry-run]
  python scripts/agent_retro.py run --all [--cap N] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- T3 티어 분류 --------------------------------------------------------

@dataclass(frozen=True)
class TierClassification:
    """SKILL 변경 후보의 중요도 분류 결과.

    tier:        "T0" | "T1" | "T2" | "T3"
    auto_apply:  T0/T1만 True (가드레일 약화는 결코 True 아님)
    reason:      티어 결정 근거 한 줄
    """

    tier: str
    auto_apply: bool
    reason: str


def classify_tier(
    *,
    kind: str,
    op: str,
    desc: str,
    weakens_guardrail: bool,
    narrows_role: bool = False,
) -> TierClassification:
    """결정적 티어 분류. 가드레일 약화는 절대 자동 적용 대상이 아니다.

    kind:  "SKILL" | "NEW_AGENT" | "TASK" | "COMPOUND"
    op:    "ADD" | "UPDATE" | "DELETE"
    """
    if weakens_guardrail:
        return TierClassification(
            "T2", False, "가드레일 약화는 최소 T2 (자동 금지)"
        )
    if kind == "NEW_AGENT":
        return TierClassification(
            "T3", False, "신규 에이전트 제창은 T3 (Owner 승인)"
        )
    if narrows_role:
        return TierClassification(
            "T2", False, "역할 축소는 T2 (CEO + Auditor)"
        )
    if kind == "SKILL" and op == "ADD":
        return TierClassification(
            "T1", True, "가법 가드레일 확장은 T1 (Lead, 자동+검토)"
        )
    if kind == "SKILL" and op == "UPDATE":
        return TierClassification(
            "T0", True, "의미 변경 없는 명료화/오타는 T0 (본인 자율)"
        )
    if kind == "SKILL" and op == "DELETE":
        return TierClassification(
            "T2", False, "삭제는 가드레일 영향 가능 — 최소 T2"
        )
    # 분류 외(TASK/Compound 후보) 또는 알 수 없는 조합은 보수적으로 T2.
    return TierClassification("T2", False, "분류 불명 — 보수적으로 T2")


# --- T5 메시지 emit (표준 스키마 정합: check_messages REQUIRED_FIELDS) ----

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_msg_id(when: _dt.datetime | None = None) -> str:
    when = when or _dt.datetime.now(_dt.timezone.utc)
    return f"MSG-{when.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _write_msg(inbox_dir: Path, mid: str, body: str) -> Path:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{mid}.md"
    path.write_text(body, encoding="utf-8")
    return path


def emit_retro_request(
    *,
    role: str,
    task_context: dict,
    inbox_dir: Path,
    task_id: str = "TASK-133",
    requester: str = "lead-engineer",
) -> Path:
    """`/retro <role>` 요청을 메시지 버스에 기록 (표준 스키마)."""
    mid = _new_msg_id()
    intent = (
        f"self-retro for role={role} "
        f"(tasks={len(task_context.get('tasks', []))}, "
        f"audits={len(task_context.get('audits', []))})"
    )
    body = (
        "---\n"
        f"id: {mid}\n"
        f"from: {requester}\n"
        f"to: {role}\n"
        f"task_id: {task_id}\n"
        f"intent: {intent}\n"
        "type: retro_request\n"
        "status: open\n"
        f"ts: {_now_iso()}\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"Self-retro requested for `{role}`.\n"
    )
    return _write_msg(inbox_dir, mid, body)


def emit_retro_reply(
    *,
    request_id: str,
    role: str,
    retro_path: Path,
    inbox_dir: Path,
    task_id: str = "TASK-133",
    recipient: str = "lead-engineer",
) -> Path:
    """retro_request 에 대한 답신 — 생성된 RETRO 파일 경로를 evidence로 첨부."""
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        f"from: {role}\n"
        f"to: {recipient}\n"
        f"task_id: {task_id}\n"
        "intent: self-retro produced\n"
        "type: retro_reply\n"
        "status: answered\n"
        f"ts: {_now_iso()}\n"
        f"in_reply_to: {request_id}\n"
        "evidence:\n"
        f"  - {retro_path.as_posix()}\n"
        "next: []\n"
        "---\n"
        f"RETRO produced at `{retro_path.as_posix()}`.\n"
    )
    return _write_msg(inbox_dir, mid, body)


def emit_escalation(
    *,
    tier: str,
    role: str,
    change_desc: str,
    inbox_dir: Path,
    task_id: str = "TASK-133",
) -> Path:
    """T2/T3 후보 SKILL 변경의 에스컬레이션.

    T3 → Owner (사람, audience: Owner)
    T2 → CEO  (자율 에이전트 + Auditor)
    audience 라우팅은 TASK-130 Owner/CEO 분리 모델에 따른다.
    """
    audience = "Owner" if tier == "T3" else "CEO"
    recipient = "owner" if tier == "T3" else "ceo"
    mid = _new_msg_id()
    body = (
        "---\n"
        f"id: {mid}\n"
        f"from: {role}\n"
        f"to: {recipient}\n"
        f"task_id: {task_id}\n"
        f"intent: SKILL change escalation ({tier})\n"
        "type: escalation\n"
        "status: open\n"
        f"ts: {_now_iso()}\n"
        f"tier: {tier}\n"
        f"audience: {audience}\n"
        "in_reply_to:\n"
        "evidence: []\n"
        "next: []\n"
        "---\n"
        f"Proposed change ({tier}): {change_desc}\n"
    )
    return _write_msg(inbox_dir, mid, body)


# --- T6 SKILL.md 가법 편집 operator + 백업 -------------------------------

@dataclass
class SkillPatchResult:
    """SKILL.md 패치 결과. 가법 적용만 auto, 그 외는 거부 (에스컬레이션 대상)."""

    applied: bool
    reason: str
    backup_path: Path | None = None


def apply_skill_patch(
    *,
    skill_path: Path,
    op: str,
    section: str,
    content: str,
    mode: str = "auto",
) -> SkillPatchResult:
    """T0/T1 가법 변경(ADD)만 auto 모드에서 적용. DELETE/UPDATE는 거부.

    백업: 적용 전 SKILL.md.bak.<timestamp> 로 원본 보존 (Mem0/cookbook rollback).
    섹션이 없으면 파일 끝에 새 ## 섹션을 만들고 content를 넣는다.
    """
    if mode == "auto" and op != "ADD":
        return SkillPatchResult(
            False,
            f"auto 모드는 ADD(가법)만 허용 — {op}는 에스컬레이션 필요",
        )
    if not skill_path.exists():
        return SkillPatchResult(False, f"SKILL not found: {skill_path}")

    original = skill_path.read_text(encoding="utf-8")
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = skill_path.with_suffix(skill_path.suffix + f".bak.{ts}")
    backup.write_text(original, encoding="utf-8")

    if op == "ADD":
        marker = f"## {section}"
        idx = original.find(marker)
        if idx == -1:
            # 섹션 없으면 파일 끝에 새 섹션을 만든다.
            new_text = (
                original.rstrip() + f"\n\n## {section}\n{content}\n"
            )
        else:
            # 다음 ## 직전(또는 EOF)까지가 그 섹션 본문.
            nxt = original.find("\n## ", idx + len(marker))
            insert_at = nxt if nxt != -1 else len(original)
            head = original[:insert_at].rstrip()
            tail = original[insert_at:]
            new_text = head + "\n" + content + "\n" + tail
        skill_path.write_text(new_text, encoding="utf-8")
        return SkillPatchResult(True, "ADD applied", backup)

    # 방어선 — 위에서 이미 차단됐어야 함.
    return SkillPatchResult(False, f"op {op} not supported in auto mode")


# --- T7 티어 라우터 -------------------------------------------------------

def route_change(
    *,
    role: str,
    skill_path: Path,
    inbox_dir: Path,
    change: dict,
) -> dict:
    """SKILL 변경 후보 한 건을 티어로 분류해 자동 적용 또는 에스컬레이션.

    T0 (자율 명료화): kind=SKILL/UPDATE — apply_skill_patch가 auto에서 거부하므로
                     실 적용 안 함, 에스컬레이션도 안 함 (본인이 수동 처리).
    T1 (가법 가드레일): kind=SKILL/ADD — auto 적용 + 백업 + AUDIT.
    T2/T3 (구조 변경): 에스컬레이션 메시지 emit, SKILL.md 무변경.

    Returns:
        {tier, reason, applied, patch_result, escalation_path}
    """
    cls = classify_tier(
        kind=change["kind"],
        op=change["op"],
        desc=change.get("desc", ""),
        weakens_guardrail=change.get("weakens_guardrail", False),
        narrows_role=change.get("narrows_role", False),
    )
    out: dict = {
        "tier": cls.tier,
        "reason": cls.reason,
        "applied": False,
        "patch_result": None,
        "escalation_path": None,
    }
    if cls.auto_apply and change["kind"] == "SKILL":
        pr = apply_skill_patch(
            skill_path=skill_path,
            op=change["op"],
            section=change.get("section", ""),
            content=change.get("content", ""),
            mode="auto",
        )
        out["patch_result"] = pr
        out["applied"] = pr.applied
        # T0(UPDATE)는 auto에서 거부됨 → applied=False, 에스컬도 안 함 (자율 영역).
    elif not cls.auto_apply:
        ep = emit_escalation(
            tier=cls.tier,
            role=role,
            change_desc=change.get("desc", ""),
            inbox_dir=inbox_dir,
        )
        out["escalation_path"] = ep
    return out


# --- T8 dispatch_reflection (mockable subagent indirection) --------------

def _render_retro_prompt(*, role: str, task_context: dict) -> str:
    """회고 서브에이전트에 줄 프롬프트 — 5-section RETRO + §5 Tier 컬럼 요구."""
    lines = [
        f"You are the {role} agent performing a self-RETRO on your recent work.",
        "Produce a RETRO with 5 sections (Korean section headers OK):",
        "  §1 Planned vs Actual / §2 Root Cause (Hansei + Blameless) /",
        "  §3 Collaboration Health Check / §4 Feedforward / §5 Forward Actions.",
        "On §5 Forward Actions, classify each item with a Tier column "
        "(T0/T1/T2/T3 for SKILL changes, — for TASK/Compound).",
        "Auto-apply tiers: T0(자기 명료화) / T1(가법 가드레일). "
        "Escalation tiers: T2(역할 축소·능력 변경) / T3(신규 에이전트·구조 변경).",
        "Never propose weakening an existing guardrail in T0/T1 — those go T2+.",
        "",
        "Recent TASKs:",
    ]
    tasks = task_context.get("tasks", [])
    if not tasks:
        lines.append("- (none)")
    for t in tasks:
        lines.append(
            f"- {t.get('id', '?')} status={t.get('status', '')} "
            f"completed_at={t.get('completed_at', '')}"
        )
    lines.append("")
    lines.append("Recent AUDITs:")
    audits = task_context.get("audits", [])
    if not audits:
        lines.append("- (none)")
    for a in audits:
        lines.append(f"- {a.get('id', '?')}")
    return "\n".join(lines)


def dispatch_reflection(
    *,
    role: str,
    task_context: dict,
    dispatcher,
) -> str:
    """회고 서브에이전트 호출. dispatcher 는 (prompt, role) -> 본문 str.

    실 사용 시 dispatcher 는 Anthropic SDK Agent tool 래퍼(주입). 단위 시험은
    fake dispatcher로 검증. None이 들어오면 명시 오류 — 라이브 모드는 외부 주입.
    """
    if dispatcher is None:
        raise RuntimeError(
            "dispatcher required — provide Agent-tool wrapper or use --dry-run path"
        )
    prompt = _render_retro_prompt(role=role, task_context=task_context)
    return dispatcher(prompt=prompt, role=role)


# --- T9 RETRO 파일 생성기 ------------------------------------------------

def write_retro_file(
    *,
    role: str,
    retros_dir: Path,
    body_md: str,
    date_str: str,
) -> Path:
    """역할별 RETRO 파일 생성. 5섹션 + §5 Tier 컬럼.

    `body_md`는 서브에이전트가 채운 §1 본문이며, §2~§5는 스켈레톤으로 둔다
    (실 사용 시 서브에이전트 응답을 섹션별로 매핑).
    """
    retros_dir.mkdir(parents=True, exist_ok=True)
    path = retros_dir / f"RETRO-{role}-{date_str}.md"
    template = (
        "---\n"
        "type: retro\n"
        f"role: {role}\n"
        f"period_end: {date_str}\n"
        f"recorded_at: {_now_iso()}\n"
        "trigger: cycle_end\n"
        "---\n\n"
        "## §1 Planned vs Actual (AAR)\n\n"
        f"{body_md}\n\n"
        "## §2 Root Cause (Hansei + Blameless)\n\n"
        "(subagent fill)\n\n"
        "## §3 Collaboration Health Check\n\n"
        "(subagent fill)\n\n"
        "## §4 Feedforward (Goldsmith)\n\n"
        "(subagent fill)\n\n"
        "## §5 Forward Actions\n\n"
        "| 종류 | 제안 | Tier | 우선순위 | Owner 제안 | 근거 |\n"
        "|------|------|------|----------|-----------|------|\n"
        "| (없음) | — | — | — | — | — |\n"
    )
    path.write_text(template, encoding="utf-8")
    return path


# --- T11 broadcast cap (safety_gate hook) --------------------------------

def broadcast_retro(
    *,
    roles: list[str],
    run_one,
    cap: int = 5,
) -> dict:
    """`/retro --all` fan-out. cap을 넘으면 skip한다.

    safety_gate `evaluate_call`은 CLI 진입점에서 1회 호출(role=lead-engineer,
    intent=retro-broadcast)해 deny면 중단; 본 함수는 그 결과를 받은 후의
    순수 fan-out 로직만 담당.
    """
    dispatched = 0
    results: list[dict] = []
    for role in roles:
        if dispatched >= cap:
            break
        results.append(run_one(role))
        dispatched += 1
    return {
        "dispatched": dispatched,
        "results": results,
        "skipped": max(0, len(roles) - dispatched),
    }

# Owner 표기는 roles.yml과 paste된 표기를 모두 허용 (frontmatter는 사람이 쓰는 자유 표기).
ROLE_OWNER_MAP: dict[str, list[str]] = {
    "backend": ["Backend Engineer", "backend"],
    "ci-cd": ["CI/CD Engineer", "ci-cd"],
    "uiux": ["UI/UX Designer", "uiux"],
    "qa": ["QA", "qa"],
    "lead-engineer": ["Lead Engineer", "lead-engineer"],
    "doc-steward": ["Doc Steward", "doc-steward"],
    "scribe": ["Scribe", "scribe"],
    "research": ["Research Agent", "research"],
    "timeline": ["Timeline Agent", "timeline"],
    "beta-tester": ["Beta Tester", "beta-tester"],
    "managing-partner": ["Managing Partner", "managing-partner"],
    "independent-auditor": ["Independent Auditor", "independent-auditor"],
}


def _frontmatter(text: str) -> dict[str, str]:
    """간이 YAML frontmatter 파서 — `key: value` 한 줄 형식만 처리."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_context(*, role: str, repo_root: Path, limit: int = 10) -> dict:
    """역할의 최근 TASK/AUDIT를 결정적으로 수집한다 (외부 부수효과 없음).

    Returns:
        {"role": role, "tasks": [{id, path, status, completed_at}, ...],
         "audits": [{id}, ...]}
    """
    owners = set(ROLE_OWNER_MAP.get(role, [role]))
    tasks_dir = repo_root / "agents/lead_engineer/tasks"
    audits_file = repo_root / "agents/lead_engineer/AUDIT-LOG.md"

    tasks: list[dict[str, str]] = []
    if tasks_dir.exists():
        for path in sorted(tasks_dir.glob("TASK-*.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            fm = _frontmatter(text)
            if fm.get("owner") in owners:
                # ID는 "TASK-NNN" 형태로 정규화.
                stem = path.stem  # e.g. "TASK-001-backend"
                parts = stem.split("-")
                tid = "-".join(parts[:2]) if len(parts) >= 2 else stem
                tasks.append(
                    {
                        "id": tid,
                        "path": str(path.relative_to(repo_root)).replace("\\", "/"),
                        "status": fm.get("status", ""),
                        "completed_at": fm.get("completed_at", ""),
                    }
                )
        tasks.sort(key=lambda t: t.get("completed_at", ""), reverse=True)
        tasks = tasks[:limit]

    audit_entries: list[dict[str, str]] = []
    if audits_file.exists():
        body = audits_file.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"### (AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\b", body):
            segment = body[match.start() : match.start() + 800]
            if any(owner in segment for owner in owners):
                audit_entries.append({"id": match.group(1)})
        audit_entries = audit_entries[-limit:]

    return {"role": role, "tasks": tasks, "audits": audit_entries}


def _repo_root() -> Path:
    """REPO_ROOT 또는 AGENT_RETRO_REPO_ROOT(시험용) 사용."""
    override = os.environ.get("AGENT_RETRO_REPO_ROOT")
    return Path(override) if override else REPO_ROOT


def _run_single(role: str, repo: Path, dry_run: bool) -> dict:
    """단일 역할 retro 실행 — dry-run 경로는 컨텍스트만 보고."""
    ctx = load_context(role=role, repo_root=repo, limit=10)
    if dry_run:
        return {
            "role": role,
            "tasks": len(ctx["tasks"]),
            "audits": len(ctx["audits"]),
            "dry_run": True,
        }
    # 실 모드는 dispatcher 주입이 필요 (Agent-tool 래퍼). 라이브 게이트는 별 task.
    return {
        "role": role,
        "tasks": len(ctx["tasks"]),
        "audits": len(ctx["audits"]),
        "note": "real-mode dispatcher injection required (library API)",
    }


def cmd_run(args: argparse.Namespace) -> int:
    repo = _repo_root()
    if args.all:
        roles = list(ROLE_OWNER_MAP.keys())
        out = broadcast_retro(
            roles=roles,
            run_one=lambda r: _run_single(r, repo, args.dry_run),
            cap=args.cap,
        )
        print(out)
        return 0
    if not args.role:
        print("error: provide <role> or --all", file=sys.stderr)
        return 2
    print(_run_single(args.role, repo, args.dry_run))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agent_retro",
        description="/retro self-improvement loop (Phase 1)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run retro on a role or all")
    run.add_argument(
        "role",
        nargs="?",
        default=None,
        help="target role id (omit when using --all)",
    )
    run.add_argument(
        "--all",
        action="store_true",
        help="broadcast retro to all known roles",
    )
    run.add_argument(
        "--cap",
        type=int,
        default=10,
        help="broadcast cap (default 10)",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="plan only; do not write SKILL/messages",
    )
    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
