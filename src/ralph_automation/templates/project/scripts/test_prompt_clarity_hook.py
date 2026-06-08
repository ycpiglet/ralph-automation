"""Unit tests for the UserPromptSubmit clarity hook (TASK-217).

Asserts the "always check, surface only when actionable" gate: render proceed →
empty context, advisory → assumption nudge, clarify → questions, scale →
/grill line. The executable hook emits the Codex hook JSON envelope and always
exits 0.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_clarity_hook as h  # noqa: E402


def _hook_context(stdout: str) -> str:
    payload = json.loads(stdout or "{}")
    return payload.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_proceed_prompt_emits_nothing():
    # clear/trivial prompt → no friction
    assert h.render("오늘 상태 한 줄로 알려줘") == ""
    assert h.render("안녕하세요") == ""


def test_clarify_prompt_emits_questions():
    out = h.render("좀 빠르게 개선해줘")
    assert "clarity" in out and "clarify" in out


def test_advisory_prompt_emits_assumption_nudge():
    # exactly one presence signal, absence signals cleared → advisory (not clarify)
    from ambiguity_scan import scan_ambiguity
    text = "fix it; done when 1 test passes. scope: only the login page"
    assert scan_ambiguity(text)["recommendation"] == "advisory"  # presence=1(it), absence cleared
    out = h.render(text)
    assert "advisory" in out or "가정" in out


def test_scale_prompt_appends_grill_line():
    out = h.render("새 시스템 아키텍처를 설계하자")
    assert "/grill" in out


def test_render_handles_empty():
    assert h.render("") == ""


class _BytesStdin:
    """Minimal stdin stub exposing .buffer (bytes) like the real OS stdin."""
    def __init__(self, text: str):
        self.buffer = io.BytesIO(text.encode("utf-8"))
    def isatty(self):
        return False


def test_main_reads_stdin_json(monkeypatch, capsys):
    payload = json.dumps({"prompt": "좀 빠르게 개선해줘"}, ensure_ascii=False)
    monkeypatch.setattr(sys, "stdin", _BytesStdin(payload))
    rc = h.main([])
    assert rc == 0
    assert "clarity" in _hook_context(capsys.readouterr().out)


def test_main_proceed_prints_nothing(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _BytesStdin(json.dumps({"prompt": "안녕하세요"})))
    assert h.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_main_text_flag(capsys):
    assert h.main(["--text", "새 플랫폼 파이프라인"]) == 0
    assert "/grill" in _hook_context(capsys.readouterr().out)


def test_main_reads_utf8_bytes_stdin_korean(monkeypatch, capsys):
    # Regression (C1): UserPromptSubmit delivers UTF-8 bytes; on Windows
    # sys.stdin.read() uses cp949 and corrupts Korean. We must read raw bytes.
    import io as _io

    class _BytesStdin:
        def __init__(self, data: bytes):
            self.buffer = _io.BytesIO(data)
        def isatty(self):
            return False
        def read(self):  # text path would corrupt — must NOT be used
            raise AssertionError("hook must read sys.stdin.buffer (bytes), not text")

    payload = json.dumps({"prompt": "새 시스템 아키텍처를 설계하자"}, ensure_ascii=False)
    monkeypatch.setattr(sys, "stdin", _BytesStdin(payload.encode("utf-8")))
    assert h.main([]) == 0
    assert "/grill" in _hook_context(capsys.readouterr().out)  # Korean scale signal survived decoding


def test_role_mention_context_appended_for_secretary():
    out = h.render("@secretary 오늘 내가 봐야 할 것")
    assert "[role-mention]" in out
    assert "worker_roles=secretary" in out
    assert "mode=chat-only" in out


def test_role_mention_context_does_not_affect_plain_prompt():
    assert h.render("안녕하세요") == ""


def test_multi_role_mention_context_is_preview():
    out = h.render("@qa @auditor 회의처럼 봐줘")
    assert "mode=meeting-preview" in out
    assert "preview first" in out


def test_meeting_mention_context_is_preview():
    out = h.render("@meeting 진행해줘")
    assert "[role-mention]" in out
    assert "mode=meeting-preview" in out
    assert "preview first" in out


def test_reporting_action_prompt_adds_bottom_line_reminder():
    out = h.render("바로 구현")
    assert "[reporting]" in out
    assert "Bottom Line:" in out
    assert "BRIEF/PLAN" in out


def test_reporting_result_prompt_adds_bottom_line_reminder():
    out = h.render("결과 보고해줘")
    assert "[reporting]" in out
    assert "Bottom Line:" in out


def test_reporting_context_coexists_with_role_mention():
    out = h.render("@secretary 오늘 내가 봐야 할 것 보고해줘")
    assert "[role-mention]" in out
    assert "[reporting]" in out


def test_reporting_scheduler_probe_prompt_adds_bottom_line_reminder():
    out = h.render("스케줄링 등록해서 우측 pane에서 동작하는지 확인해봐")
    assert "[reporting]" in out
    assert "Bottom Line:" in out


def test_reporting_continue_next_work_prompt_adds_bottom_line_reminder():
    out = h.render("다음 작업 이어가고 필요한 테스트까지 해줘")
    assert "[reporting]" in out
    assert "Bottom Line:" in out
