"""secretary_digest render 테스트 (TASK-226).

render_digest 는 주입된 데이터만으로 동작(repo 비의존) — 제2 집계기 없이 collect()/SCHEDULE
출력을 그대로 렌더하는지, Owner 결정 대기 추출이 맞는지 검증.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_secd", ROOT / "scripts" / "secretary_digest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


DATA = {
    "open_tasks": [
        {"id": "TASK-300", "status": "대기", "priority": "High", "owner": "Lead", "tags": ["a"]},
        {"id": "TASK-301", "status": "보류", "priority": "Low", "owner": "QA", "tags": ["b"],
         "gate": "Owner 착수 판단"},
        {"id": "TASK-302", "status": "대기", "priority": "Critical", "owner": "Lead", "tags": ["c"]},
        {"id": "TASK-303", "status": "보류", "priority": "Low", "owner": "Lead", "tags": ["deferred"],
         "gate": "Owner 착수 판단 — 현 단일 러너엔 불필요. 3회 deferred."},
        {"id": "TASK-304", "status": "대기", "priority": "Medium", "owner": "Lead", "tags": ["ops"],
         "gate": "R3 전 Owner 확인"},
        {"id": "TASK-305", "status": "보류", "priority": "High", "owner": "Lead", "tags": ["provider"],
         "gate": "외부 계정 결제 필요"},
    ],
    "due_checks": {"scribe": "[scribe_due] overdue — 압축 필요"},
    "doc_health": "WARN 9",
}
SCHEDULES = [
    {"id": "daily-digest", "cron": "0 9 * * *", "selector": "digest", "mode": "notify", "enabled": True},
    {"id": "off-one", "cron": "0 0 * * *", "selector": "maintenance", "mode": "pr", "enabled": False},
]


def test_render_has_all_sections():
    secd = _load()
    out = secd.render_digest(DATA, SCHEDULES, "2026-06-04")
    for section in ("# DIGEST 2026-06-04", "Bottom Line:", "## 내 결정이 필요한 것",
                    "## 열린 작업", "## 예정 스케줄", "## 리스크 / 주기 신호"):
        assert section in out, section


def test_owner_decisions_picks_holds_and_owner_gate():
    secd = _load()
    decisions = secd._owner_decisions(DATA["open_tasks"])
    ids = {d["id"] for d in decisions}
    assert ids == {"TASK-304", "TASK-305"}  # REVIEW/ASK only; DEFER/ACT 후보는 Owner 결정 대기가 아님


def test_digest_owner_count_matches_backlog_ask_review_not_defer():
    secd = _load()
    out = secd.render_digest(DATA, SCHEDULES, "2026-06-04")
    assert "Owner 결정 대기 2건" in out
    decisions = out.split("## 내 결정이 필요한 것")[1].split("## 열린 작업")[0]
    assert "TASK-304" in decisions
    assert "TASK-305" in decisions
    assert "TASK-301" not in decisions
    assert "TASK-303" not in decisions


def test_priority_sort_in_render():
    secd = _load()
    out = secd.render_digest(DATA, SCHEDULES, "2026-06-04")
    # 열린 작업 섹션에서 Critical(302)이 High(300)보다 먼저
    body = out.split("## 열린 작업")[1]
    assert body.index("TASK-302") < body.index("TASK-300")


def test_only_enabled_schedules_shown():
    secd = _load()
    out = secd.render_digest(DATA, SCHEDULES, "2026-06-04")
    sched = out.split("## 예정 스케줄")[1].split("## 리스크")[0]
    assert "daily-digest" in sched and "off-one" not in sched


def test_empty_inputs_no_crash():
    secd = _load()
    out = secd.render_digest({"open_tasks": [], "due_checks": {}, "doc_health": "0"}, [], "2026-06-04")
    assert "열린 작업 0건" in out and "(활성 스케줄 없음" in out
