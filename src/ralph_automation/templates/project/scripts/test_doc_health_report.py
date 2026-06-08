from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("doc_health_report.py")
SPEC = importlib.util.spec_from_file_location("doc_health_report", MODULE_PATH)
doc_health_report = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["doc_health_report"] = doc_health_report
SPEC.loader.exec_module(doc_health_report)


def write_message(root: Path, name: str, body: str) -> None:
    inbox = root / "agents" / "messages" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(body, encoding="utf-8")


def test_parse_frontmatter_inline_list():
    meta = doc_health_report.parse_frontmatter(
        "---\n"
        "id: TASK-128\n"
        "tags: [docs, governance]\n"
        "---\n"
        "body\n"
    )

    assert meta["id"] == "TASK-128"
    assert meta["tags"] == ["docs", "governance"]


def test_message_orphan_is_reported(tmp_path):
    write_message(
        tmp_path,
        "MSG-20260527-171000-abcdef.md",
        "---\n"
        "id: MSG-20260527-171000-abcdef\n"
        "from: qa\n"
        "to: lead-engineer\n"
        "task_id: TASK-128\n"
        "intent: orphan reply\n"
        "type: reply\n"
        "status: open\n"
        "ts: 2026-05-27T17:10:00+09:00\n"
        "in_reply_to: MSG-20260527-170000-000000\n"
        "---\n"
        "reply\n",
    )

    findings = doc_health_report.check_message_health(
        tmp_path,
        now=dt.datetime.fromisoformat("2026-05-27T17:20:00+09:00"),
    )

    assert any(f.code == "message-orphan" and f.severity == "ERROR" for f in findings)


def test_stale_open_sample_messages_are_ignored(tmp_path):
    samples = tmp_path / "agents" / "messages" / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    (samples / "MSG-20260522-091207-a1b2c3.md").write_text(
        "---\n"
        "id: MSG-20260522-091207-a1b2c3\n"
        "from: sample\n"
        "to: lead-engineer\n"
        "task_id: TASK-001\n"
        "intent: sample\n"
        "type: request\n"
        "status: open\n"
        "ts: 2026-05-22T09:12:07+09:00\n"
        "---\n"
        "sample\n",
        encoding="utf-8",
    )

    findings = doc_health_report.check_message_health(
        tmp_path,
        now=dt.datetime.fromisoformat("2026-06-06T01:00:00+09:00"),
    )

    assert not [f for f in findings if f.code == "message-stale-open"], findings


def test_overall_status_prefers_errors():
    findings = [
        doc_health_report.Finding("WARN", "one", "x", "warn"),
        doc_health_report.Finding("ERROR", "two", "x", "error"),
    ]

    assert doc_health_report.overall_status(findings) == "R"


def _write_task(root, name, status, drop_keys=()):
    d = root / "agents" / "lead_engineer" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    keys = {
        "type": "task", "id": name[:-3], "status": status, "owner": "Lead Engineer",
        "assignees": "[Lead Engineer]", "priority": "Low", "difficulty": "낮",
        "est_hours": "1", "est_tokens": "1000", "tags": "[x]",
        "trigger_meeting": "MEETING-x", "audit_log": "AUDIT-x",
    }
    fm = "\n".join(f"{k}: {v}" for k, v in keys.items() if k not in drop_keys)
    (d / name).write_text(f"---\n{fm}\n---\nbody\n", encoding="utf-8")


def test_frontmatter_gap_skips_completed_tasks(tmp_path):
    # completed (frozen) tasks must NOT be flagged for frontmatter gaps (TASK-219:
    # check_agent_docs already gated them at completion → advisory noise otherwise).
    _write_task(tmp_path, "TASK-900.md", "완료", drop_keys=("assignees",))
    findings = doc_health_report.check_task_frontmatter_gaps(tmp_path)
    assert not [f for f in findings if f.code == "task-frontmatter-gap"], findings


def test_frontmatter_gap_still_flags_active_tasks(tmp_path):
    _write_task(tmp_path, "TASK-901.md", "진행 중", drop_keys=("assignees",))
    findings = doc_health_report.check_task_frontmatter_gaps(tmp_path)
    assert any(f.code == "task-frontmatter-gap" for f in findings)


def _write_cycle(root, cycle_id, status="완료"):
    d = root / "agents" / "lead_engineer"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"CYCLE-{cycle_id:03d}.md").write_text(
        f"# CYCLE-{cycle_id:03d}\n\n상태: {status}\n", encoding="utf-8"
    )


def test_missing_review_exempts_legacy_cycles(tmp_path):
    # REVIEW 관행 도입 전(CYCLE < 10) 완료 사이클은 리뷰가 없어도 면제(COMPOUND-030 — frozen 백필 금지).
    _write_cycle(tmp_path, 3)  # 완료, REVIEW 없음 → 면제
    findings = doc_health_report.check_missing_review_files(tmp_path)
    assert not [f for f in findings if f.code == "missing-review"], findings


def test_missing_review_still_flags_modern_cycles(tmp_path):
    # >= 10 완료 사이클의 리뷰 누락은 ERROR 로 잡아야 한다.
    _write_cycle(tmp_path, 50)  # 완료, REVIEW 없음 → ERROR
    findings = doc_health_report.check_missing_review_files(tmp_path)
    assert any(f.code == "missing-review" and f.severity == "ERROR" for f in findings), findings
