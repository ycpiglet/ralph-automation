from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_mentions as rm  # noqa: E402


def test_no_mentions_is_empty():
    result = rm.analyze("안녕하세요")
    assert result["has_signal"] is False
    assert result["mode"] == "none"
    assert result["worker_roles"] == []
    assert result["perspective_subagents"] == []
    assert result["non_worker"] == []


def test_secretary_mention_routes_chat_only():
    result = rm.analyze("@secretary 오늘 내가 봐야 할 것만 정리해줘")
    assert result["has_signal"] is True
    assert result["mode"] == "chat-only"
    assert result["worker_roles"] == ["secretary"]
    assert "secretary" in result["all_roles"]


def test_secretary_phrase_routes_without_at():
    result = rm.analyze("오늘 내가 볼 것과 Owner 결정 사항 정리해줘")
    assert result["has_signal"] is True
    assert result["mode"] == "chat-only"
    assert result["worker_roles"] == ["secretary"]
    assert result["triggers"] == ["secretary-phrase"]


def test_ceo_and_lead_are_worker_roles():
    result = rm.analyze("@ceo @lead 이 방향과 구현 계획을 나눠서 봐줘")
    assert result["mode"] == "meeting-preview"
    assert result["worker_roles"] == ["ceo", "lead-engineer"]
    assert result["perspective_subagents"] == []


def test_standalone_meeting_tag_is_preview():
    result = rm.analyze("@meeting 진행해줘")
    assert result["has_signal"] is True
    assert result["mode"] == "meeting-preview"
    assert result["worker_roles"] == []
    assert result["perspective_subagents"] == []


def test_reviewer_and_skeptic_are_perspective_subagents():
    result = rm.analyze("@reviewer @skeptic 이 설계의 허점을 봐줘")
    assert result["mode"] == "meeting-preview"
    assert result["worker_roles"] == []
    assert result["perspective_subagents"] == ["reviewer", "skeptic"]


def test_owner_is_non_worker_escalation_context():
    result = rm.analyze("@owner 이건 승인 필요해?")
    assert result["mode"] == "chat-only"
    assert result["worker_roles"] == []
    assert result["non_worker"] == ["owner"]
    assert "Owner escalation" in result["notes"][0]


def test_record_words_change_mode_without_writing_files():
    result = rm.analyze("@qa 이 검토를 message bus에 기록 남겨")
    assert result["mode"] == "record-call"
    assert result["worker_roles"] == ["qa"]
    assert result["writes_files"] is False
    assert "explicit execution path required" in result["notes"]


def test_render_context_for_single_role():
    ctx = rm.render_context("@secretary 오늘 내가 봐야 할 것")
    assert "[role-mention]" in ctx
    assert "mode=chat-only" in ctx
    assert "worker_roles=secretary" in ctx
    assert "R1" in ctx


def test_render_context_for_multi_role_preview():
    ctx = rm.render_context("@qa @auditor 회의처럼 봐줘")
    assert "mode=meeting-preview" in ctx
    assert "worker_roles=qa" in ctx
    assert "perspective_subagents=auditor" in ctx
    assert "preview first" in ctx
