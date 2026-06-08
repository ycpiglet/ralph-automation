"""TASK-133 — /retro 자기개선 루프 단위 시험."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_help_lists_subcommands():
    r = subprocess.run(
        [sys.executable, "scripts/agent_retro.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "retro" in r.stdout.lower()
    assert "--all" in r.stdout or "role" in r.stdout.lower()


def test_load_context_for_role_returns_recent_artifacts(tmp_path):
    """역할별 최근 TASK/AUDIT 결정적 수집."""
    repo = tmp_path
    (repo / "agents/lead_engineer/tasks").mkdir(parents=True)
    (repo / "agents/lead_engineer/tasks/TASK-001-backend.md").write_text(
        "---\n"
        "owner: Backend Engineer\n"
        "status: 완료\n"
        "completed_at: 2026-05-27T00:00:00+09:00\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    (repo / "agents/lead_engineer/AUDIT-LOG.md").write_text(
        "### AUDIT-2026-05-27-001\n수행자: Backend Engineer\nbody\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import load_context

    ctx = load_context(role="backend", repo_root=repo, limit=5)
    assert ctx["role"] == "backend"
    assert any("TASK-001" in t["id"] for t in ctx["tasks"])
    assert any("AUDIT-2026-05-27-001" in a["id"] for a in ctx["audits"])


def test_load_context_ignores_other_roles(tmp_path):
    """다른 owner의 TASK는 수집하지 않음."""
    repo = tmp_path
    (repo / "agents/lead_engineer/tasks").mkdir(parents=True)
    (repo / "agents/lead_engineer/tasks/TASK-001-uiux.md").write_text(
        "---\nowner: UI/UX Designer\nstatus: 완료\n"
        "completed_at: 2026-05-27T00:00:00+09:00\n---\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import load_context

    ctx = load_context(role="backend", repo_root=repo, limit=5)
    assert ctx["tasks"] == []


# --- T3 티어 분류기 -------------------------------------------------------

def _import_classify_tier():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import classify_tier  # noqa: WPS433

    return classify_tier


def test_classify_tier_cosmetic_is_T0():
    classify_tier = _import_classify_tier()
    c = classify_tier(
        kind="SKILL", op="UPDATE", desc="typo fix in heading",
        weakens_guardrail=False,
    )
    assert c.tier == "T0"
    assert c.auto_apply is True


def test_classify_tier_guardrail_add_is_T1():
    classify_tier = _import_classify_tier()
    c = classify_tier(
        kind="SKILL", op="ADD", desc="forbid running tests without permission",
        weakens_guardrail=False,
    )
    assert c.tier == "T1"
    assert c.auto_apply is True


def test_classify_tier_role_narrowing_is_T2():
    classify_tier = _import_classify_tier()
    c = classify_tier(
        kind="SKILL", op="UPDATE", desc="remove deployment authority from role",
        weakens_guardrail=False, narrows_role=True,
    )
    assert c.tier == "T2"
    assert c.auto_apply is False


def test_classify_tier_new_agent_is_T3():
    classify_tier = _import_classify_tier()
    c = classify_tier(
        kind="NEW_AGENT", op="ADD", desc="propose translator agent",
        weakens_guardrail=False,
    )
    assert c.tier == "T3"
    assert c.auto_apply is False


def test_guardrail_weakening_blocked_from_auto_apply():
    """가드레일 약화는 절대 T0/T1 자동 적용 금지."""
    classify_tier = _import_classify_tier()
    c = classify_tier(
        kind="SKILL", op="DELETE", desc="loosen audit gate",
        weakens_guardrail=True,
    )
    assert c.tier in {"T2", "T3"}
    assert c.auto_apply is False


# --- T4 check_messages TYPE_ENUM 확장 ------------------------------------

def test_check_messages_accepts_retro_types():
    """retro_request/retro_reply가 메시지 type enum에 포함되어야 함."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_messages import TYPE_ENUM  # noqa: WPS433

    for t in ("retro_request", "retro_reply"):
        assert t in TYPE_ENUM, f"{t} must be in TYPE_ENUM"
    # escalation은 기존부터 존재해야 함 (재확인).
    assert "escalation" in TYPE_ENUM


# --- T5 메시지 emit (표준 스키마 정합) ------------------------------------

def _import_emit():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import (  # noqa: WPS433
        emit_escalation,
        emit_retro_reply,
        emit_retro_request,
    )

    return emit_retro_request, emit_retro_reply, emit_escalation


def _run_check_messages(inbox_dir: Path) -> tuple[int, str]:
    """단일 inbox 디렉토리에 check_messages를 돌려 returncode/stdout 반환."""
    r = subprocess.run(
        [sys.executable, "scripts/check_messages.py", "--inbox", str(inbox_dir)],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    return r.returncode, r.stdout + r.stderr


def test_emit_retro_request_lint_clean(tmp_path):
    emit_retro_request, _, _ = _import_emit()
    inbox = tmp_path / "inbox"
    p = emit_retro_request(
        role="backend",
        task_context={"tasks": [], "audits": []},
        inbox_dir=inbox,
    )
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # 표준 스키마: id/from/to/task_id/intent/type/status/ts (REQUIRED_FIELDS)
    for field in ("id:", "from:", "to: backend", "intent:", "type: retro_request",
                  "status: open", "ts:"):
        assert field in text, f"missing field: {field}"


def test_emit_retro_reply_in_reply_to(tmp_path):
    _, emit_retro_reply, _ = _import_emit()
    inbox = tmp_path / "inbox"
    p = emit_retro_reply(
        request_id="MSG-20260528-000000-aaaaaa",
        role="backend",
        retro_path=Path("agents/backend_engineer/retros/RETRO-backend-2026-05-28.md"),
        inbox_dir=inbox,
    )
    text = p.read_text(encoding="utf-8")
    assert "type: retro_reply" in text
    assert "in_reply_to: MSG-20260528-000000-aaaaaa" in text
    assert "from: backend" in text
    assert "status: answered" in text


def test_emit_escalation_T3_routes_to_owner(tmp_path):
    _, _, emit_escalation = _import_emit()
    inbox = tmp_path / "inbox"
    p = emit_escalation(
        tier="T3", role="backend",
        change_desc="propose translator agent",
        inbox_dir=inbox,
    )
    text = p.read_text(encoding="utf-8")
    assert "type: escalation" in text
    assert "audience: Owner" in text
    assert "tier: T3" in text


def test_emit_escalation_T2_routes_to_ceo(tmp_path):
    _, _, emit_escalation = _import_emit()
    inbox = tmp_path / "inbox"
    p = emit_escalation(
        tier="T2", role="backend",
        change_desc="narrow role authority",
        inbox_dir=inbox,
    )
    text = p.read_text(encoding="utf-8")
    assert "audience: CEO" in text
    assert "tier: T2" in text


# --- T6 SKILL.md 가법 편집 operator + 백업 -------------------------------

def _import_apply_skill_patch():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import apply_skill_patch  # noqa: WPS433

    return apply_skill_patch


def test_apply_skill_patch_adds_guardrail_with_backup(tmp_path):
    apply_skill_patch = _import_apply_skill_patch()
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "# X\n\n## 책임\n- a\n\n## 금지\n- b\n", encoding="utf-8",
    )
    result = apply_skill_patch(
        skill_path=skill, op="ADD", section="금지",
        content="- 새 가드레일: do not foo",
    )
    assert result.applied is True
    new_text = skill.read_text(encoding="utf-8")
    assert "do not foo" in new_text
    # 백업 파일이 존재해야 함
    backups = list(tmp_path.glob("SKILL.md.bak.*"))
    assert backups, "백업 파일이 생성되어야 함"
    assert result.backup_path is not None and result.backup_path.exists()


def test_apply_skill_patch_creates_section_if_missing(tmp_path):
    apply_skill_patch = _import_apply_skill_patch()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n\n## 책임\n- a\n", encoding="utf-8")
    result = apply_skill_patch(
        skill_path=skill, op="ADD", section="금지",
        content="- forbid X",
    )
    assert result.applied is True
    text = skill.read_text(encoding="utf-8")
    assert "## 금지" in text and "forbid X" in text


def test_apply_skill_patch_refuses_delete_in_auto_mode(tmp_path):
    apply_skill_patch = _import_apply_skill_patch()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n\n## 금지\n- b\n", encoding="utf-8")
    result = apply_skill_patch(
        skill_path=skill, op="DELETE", section="금지",
        content="- b", mode="auto",
    )
    assert result.applied is False
    assert "auto" in result.reason.lower() or "DELETE" in result.reason
    # 파일이 변경되지 않아야 함
    assert "- b" in skill.read_text(encoding="utf-8")


def test_apply_skill_patch_refuses_update_in_auto_mode(tmp_path):
    apply_skill_patch = _import_apply_skill_patch()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n\n## 금지\n- b\n", encoding="utf-8")
    result = apply_skill_patch(
        skill_path=skill, op="UPDATE", section="금지",
        content="- new", mode="auto",
    )
    assert result.applied is False


def test_apply_skill_patch_missing_file(tmp_path):
    apply_skill_patch = _import_apply_skill_patch()
    skill = tmp_path / "NOPE.md"
    result = apply_skill_patch(
        skill_path=skill, op="ADD", section="금지", content="- x",
    )
    assert result.applied is False
    assert "not found" in result.reason.lower() or "없" in result.reason


# --- T7 티어 라우터 -------------------------------------------------------

def _import_route_change():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import route_change  # noqa: WPS433

    return route_change


def test_route_change_T1_applies_to_skill(tmp_path):
    route_change = _import_route_change()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n\n## 금지\n- a\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    out = route_change(
        role="backend",
        skill_path=skill,
        inbox_dir=inbox,
        change={
            "kind": "SKILL", "op": "ADD",
            "section": "금지", "content": "- forbid foo",
            "desc": "new guardrail",
            "weakens_guardrail": False,
        },
    )
    assert out["tier"] == "T1"
    assert out["applied"] is True
    assert out["escalation_path"] is None
    assert "forbid foo" in skill.read_text(encoding="utf-8")


def test_route_change_T3_emits_escalation_no_apply(tmp_path):
    route_change = _import_route_change()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    out = route_change(
        role="backend",
        skill_path=skill,
        inbox_dir=inbox,
        change={
            "kind": "NEW_AGENT", "op": "ADD",
            "section": "", "content": "",
            "desc": "propose translator agent",
            "weakens_guardrail": False,
        },
    )
    assert out["tier"] == "T3"
    assert out["applied"] is False
    assert out["escalation_path"] is not None
    text = out["escalation_path"].read_text(encoding="utf-8")
    assert "audience: Owner" in text
    # SKILL.md는 변경되지 않아야 함
    assert skill.read_text(encoding="utf-8") == "# X\n"


def test_route_change_T2_role_narrowing_escalates_to_ceo(tmp_path):
    route_change = _import_route_change()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    out = route_change(
        role="backend",
        skill_path=skill,
        inbox_dir=inbox,
        change={
            "kind": "SKILL", "op": "UPDATE",
            "section": "권한", "content": "",
            "desc": "remove deploy authority",
            "weakens_guardrail": False,
            "narrows_role": True,
        },
    )
    assert out["tier"] == "T2"
    assert out["applied"] is False
    text = out["escalation_path"].read_text(encoding="utf-8")
    assert "audience: CEO" in text


def test_route_change_T0_cosmetic_does_not_auto_apply_update(tmp_path):
    """T0(UPDATE)는 분류상 auto_apply=True지만 SKILL ADD-only 정책으로 실제 적용은 안 됨.

    이 경계 케이스에선 patch_result.applied=False지만 escalation도 emit 안 함 (의도된 무손실).
    """
    route_change = _import_route_change()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# X\n\n## 금지\n- a\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    out = route_change(
        role="backend",
        skill_path=skill,
        inbox_dir=inbox,
        change={
            "kind": "SKILL", "op": "UPDATE",
            "section": "금지", "content": "- a (typo fix)",
            "desc": "typo",
            "weakens_guardrail": False,
        },
    )
    # 티어는 T0지만 실 적용은 안 됨 (Mem0식 안전: UPDATE는 본인 검토 후 수동)
    assert out["tier"] == "T0"
    assert out["applied"] is False
    assert out["escalation_path"] is None  # T0는 자기 자율 영역 — 에스컬 없음
    # SKILL.md 무손상
    assert skill.read_text(encoding="utf-8") == "# X\n\n## 금지\n- a\n"


# --- T8 dispatch_reflection (mockable) -----------------------------------

def _import_dispatch_reflection():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import dispatch_reflection  # noqa: WPS433

    return dispatch_reflection


def test_dispatch_reflection_calls_dispatcher_with_role_and_context():
    dispatch_reflection = _import_dispatch_reflection()
    captured: dict = {}

    def fake(*, prompt: str, role: str) -> str:
        captured["prompt"] = prompt
        captured["role"] = role
        return "RETRO-BODY-MOCK"

    out = dispatch_reflection(
        role="backend",
        task_context={"tasks": [{"id": "TASK-049", "status": "완료"}],
                      "audits": [{"id": "AUDIT-2026-05-27-016"}]},
        dispatcher=fake,
    )
    assert out == "RETRO-BODY-MOCK"
    assert captured["role"] == "backend"
    assert "TASK-049" in captured["prompt"]
    assert "AUDIT-2026-05-27-016" in captured["prompt"]


def test_dispatch_reflection_raises_without_dispatcher():
    dispatch_reflection = _import_dispatch_reflection()
    with pytest.raises(RuntimeError):
        dispatch_reflection(
            role="backend",
            task_context={"tasks": [], "audits": []},
            dispatcher=None,
        )


# --- T9 RETRO 파일 생성기 + §5 Tier 컬럼 -----------------------------------

def _import_write_retro_file():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import write_retro_file  # noqa: WPS433

    return write_retro_file


def test_write_retro_file_5_sections_and_tier_column(tmp_path):
    write_retro_file = _import_write_retro_file()
    p = write_retro_file(
        role="backend",
        retros_dir=tmp_path / "retros",
        body_md="(stub)",
        date_str="2026-05-28",
    )
    assert p.exists()
    assert p.name == "RETRO-backend-2026-05-28.md"
    txt = p.read_text(encoding="utf-8")
    for sec in (
        "§1 Planned vs Actual",
        "§2 Root Cause",
        "§3 Collaboration Health Check",
        "§4 Feedforward",
        "§5 Forward Actions",
    ):
        assert sec in txt, f"missing section: {sec}"
    # Tier 컬럼이 §5 표 헤더에 존재해야 함
    assert "| Tier |" in txt


# --- T10 promote_retro_forward Tier 컬럼 인식 -----------------------------

def _import_collect_forward():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from promote_retro_forward import parse_forward_section  # noqa: WPS433

    return parse_forward_section


def test_promote_recognizes_tier_column_6col(tmp_path):
    """6컬럼 (Tier 포함) 신 포맷 인식."""
    parse_forward_section = _import_collect_forward()
    retro_path = tmp_path / "RETRO-backend-2026-05-28.md"
    retro_path.write_text(
        "---\nrole: backend\nperiod_end: 2026-05-28\n---\n\n"
        "## §5 Forward Actions\n\n"
        "| 종류 | 제안 | Tier | 우선순위 | Owner 제안 | 근거 |\n"
        "|------|------|------|----------|-----------|------|\n"
        "| SKILL 갱신 | forbid foo | T1 | — | 본인 | §4 |\n",
        encoding="utf-8",
    )
    items = parse_forward_section(retro_path)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "SKILL 갱신"
    assert item.proposal == "forbid foo"
    assert item.tier == "T1"
    assert item.priority == "—"


def test_promote_still_accepts_legacy_5col(tmp_path):
    """구 포맷(5컬럼, Tier 없음) 호환 — tier 필드는 '—'으로 채워짐."""
    parse_forward_section = _import_collect_forward()
    retro_path = tmp_path / "RETRO-uiux-2026-05-21.md"
    retro_path.write_text(
        "---\nrole: uiux\nperiod_end: 2026-05-21\n---\n\n"
        "## §5 Forward Actions\n\n"
        "| 종류 | 제안 | 우선순위 | Owner 제안 | 근거 |\n"
        "|------|------|----------|-----------|------|\n"
        "| TASK 후보 | i18n 정리 | High | UI/UX Designer | §4 |\n",
        encoding="utf-8",
    )
    items = parse_forward_section(retro_path)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "TASK 후보"
    assert item.priority == "High"
    assert item.tier == "—"


# --- T11 broadcast cap ----------------------------------------------------

def _import_broadcast_retro():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import broadcast_retro  # noqa: WPS433

    return broadcast_retro


def test_broadcast_respects_cap():
    broadcast_retro = _import_broadcast_retro()
    calls: list[str] = []

    def fake_run_one(role: str) -> dict:
        calls.append(role)
        return {"role": role, "ok": True}

    roles = ["backend", "ci-cd", "uiux", "qa", "lead-engineer"]
    out = broadcast_retro(roles=roles, run_one=fake_run_one, cap=3)
    assert out["dispatched"] == 3
    assert out["skipped"] == 2
    assert len(calls) == 3
    assert calls == roles[:3]


def test_broadcast_zero_cap_skips_all():
    broadcast_retro = _import_broadcast_retro()
    out = broadcast_retro(roles=["a", "b"], run_one=lambda r: {}, cap=0)
    assert out["dispatched"] == 0
    assert out["skipped"] == 2


# --- T12 CLI wiring (dry-run, single + --all) -----------------------------

def test_cli_run_role_dry_run_does_not_mutate(tmp_path):
    """dry-run은 SKILL/메시지를 쓰지 않고 컨텍스트만 보고."""
    repo = tmp_path
    (repo / "agents/lead_engineer/tasks").mkdir(parents=True)
    (repo / "agents/lead_engineer/tasks/TASK-001-backend.md").write_text(
        "---\nowner: Backend Engineer\nstatus: 완료\n"
        "completed_at: 2026-05-27T00:00:00+09:00\n---\n",
        encoding="utf-8",
    )
    skill = repo / "agents/backend_engineer/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Backend\n\n## 금지\n- a\n", encoding="utf-8")

    env = os.environ.copy()
    env["AGENT_RETRO_REPO_ROOT"] = str(repo)
    r = subprocess.run(
        [sys.executable, "scripts/agent_retro.py", "run", "backend", "--dry-run"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    # SKILL.md 무변경
    assert skill.read_text(encoding="utf-8") == "# Backend\n\n## 금지\n- a\n"
    # 출력에 role과 dry-run 표시
    assert "backend" in r.stdout
    assert "dry" in r.stdout.lower()


def test_cli_run_all_dry_run_lists_roles(tmp_path):
    env = os.environ.copy()
    env["AGENT_RETRO_REPO_ROOT"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, "scripts/agent_retro.py", "run", "--all",
         "--cap", "3", "--dry-run"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    # cap=3 → 3개 dispatched
    assert "dispatched" in r.stdout.lower() or "3" in r.stdout


def test_cli_run_requires_role_or_all():
    r = subprocess.run(
        [sys.executable, "scripts/agent_retro.py", "run"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "role" in r.stderr.lower() or "all" in r.stderr.lower()


# --- T13 통합 시험: T1 자동 적용 + T3 에스컬레이션 한 사이클 -----------------

def test_end_to_end_retro_cycle(tmp_path):
    """1개 RETRO 사이클: T1 가법 적용(SKILL 변경) + T3 신규 에이전트 에스컬."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_retro import (  # noqa: WPS433
        emit_retro_reply,
        emit_retro_request,
        load_context,
        route_change,
        write_retro_file,
    )

    repo = tmp_path
    (repo / "agents/backend_engineer").mkdir(parents=True)
    skill = repo / "agents/backend_engineer/SKILL.md"
    skill.write_text("# Backend\n\n## 금지\n- a\n", encoding="utf-8")
    retros_dir = repo / "agents/backend_engineer/retros"
    retros_dir.mkdir()
    inbox = repo / "agents/messages/inbox"
    inbox.mkdir(parents=True)
    (repo / "agents/lead_engineer").mkdir(parents=True, exist_ok=True)
    (repo / "agents/lead_engineer/AUDIT-LOG.md").write_text("", encoding="utf-8")

    # 1. 컨텍스트 수집 → retro_request emit
    ctx = load_context(role="backend", repo_root=repo, limit=5)
    req_path = emit_retro_request(role="backend", task_context=ctx, inbox_dir=inbox)
    assert req_path.exists()

    # 2. RETRO 파일 생성 (서브에이전트 응답은 mock body로 대체)
    retro_path = write_retro_file(
        role="backend", retros_dir=retros_dir,
        body_md="(mock body)", date_str="2026-05-28",
    )
    assert retro_path.exists()

    # 3. §5 후보 2건 — T1 가법(자동 적용) + T3 신규 에이전트(에스컬)
    r_t1 = route_change(
        role="backend", skill_path=skill, inbox_dir=inbox,
        change={
            "kind": "SKILL", "op": "ADD",
            "section": "금지", "content": "- forbid foo",
            "desc": "new guardrail", "weakens_guardrail": False,
        },
    )
    r_t3 = route_change(
        role="backend", skill_path=skill, inbox_dir=inbox,
        change={
            "kind": "NEW_AGENT", "op": "ADD",
            "section": "", "content": "",
            "desc": "propose translator agent",
            "weakens_guardrail": False,
        },
    )

    # 4. retro_reply emit
    reply = emit_retro_reply(
        request_id=req_path.stem, role="backend",
        retro_path=retro_path, inbox_dir=inbox,
    )

    # --- 검증 ---
    # T1: SKILL.md 에 가드레일 추가됨
    assert r_t1["tier"] == "T1"
    assert r_t1["applied"] is True
    assert "forbid foo" in skill.read_text(encoding="utf-8")
    # 백업 파일 존재
    assert list((repo / "agents/backend_engineer").glob("SKILL.md.bak.*"))

    # T3: SKILL 변경 없이 Owner 에스컬레이션
    assert r_t3["tier"] == "T3"
    assert r_t3["applied"] is False
    assert r_t3["escalation_path"] is not None
    assert "audience: Owner" in r_t3["escalation_path"].read_text(encoding="utf-8")

    # 메시지 3건 (request + escalation + reply) 모두 inbox에 존재
    msgs = sorted(inbox.glob("MSG-*.md"))
    assert len(msgs) == 3
    assert all(m.exists() for m in msgs)
    assert reply.exists()
