#!/usr/bin/env python3
"""UserPromptSubmit clarity hook (TASK-217) — always-check, surface-only-when-actionable.

What it does: reads the user's prompt (UserPromptSubmit passes JSON on stdin with a
`prompt` field, or use --text), runs the mechanical ambiguity scan, and emits a SHORT
context line for the assistant ONLY when action is warranted. This is the "always
check, but silent unless ambiguous" gate the Owner chose (decision 2):

  - recommendation == proceed  → emit NOTHING (no friction on clear/trivial prompts)
  - recommendation == advisory → emit a one-line "state assumptions" nudge
  - recommendation == clarify  → emit the fired signals + "ask a batch before acting"
  - grill_suggested (large change) → append a "/grill (heavy mode) available" line

Owner-enable (settings.json — NOT self-wired, R3 boundary):
  "hooks": { "UserPromptSubmit": [ { "hooks": [
    { "type": "command", "command": "python scripts/prompt_clarity_hook.py" } ] } ] }

Exit 0 always (never block a prompt). Output on stdout becomes assistant context.
Best-effort: any error → silent exit 0 (never disrupt the session).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


_REPORTING_TRIGGERS = (
    "바로 구현",
    "구현",
    "수정",
    "고쳐",
    "진행",
    "이어가",
    "다음 작업",
    "마무리",
    "완료",
    "끝났",
    "결과",
    "보고",
    "검증",
    "확인",
    "테스트",
    "실행",
    "동작",
    "스케줄링",
    "작업 할당",
    "보강",
    "개선",
    "compound",
    "규칙 강화",
)


def _needs_reporting_reminder(prompt: str) -> bool:
    text = prompt.lower()
    return any(trigger.lower() in text for trigger in _REPORTING_TRIGGERS)


def _read_prompt(argv) -> str:
    # --text "..." for testing; otherwise UserPromptSubmit JSON on stdin.
    if len(argv) >= 2 and argv[0] == "--text":
        return argv[1]
    # Read RAW BYTES and decode UTF-8 explicitly. UserPromptSubmit delivers UTF-8,
    # but on Windows sys.stdin.read() uses the locale codepage (cp949) and would
    # corrupt Korean prompts → the always-on check would silently miss them.
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace") if not sys.stdin.isatty() else ""
    except Exception:
        raw = ""
    if not raw.strip():
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("prompt") or data.get("user_prompt") or data.get("text") or "")
    except Exception:
        pass
    return raw  # plain text fallback


def render(prompt: str) -> str:
    """Return the context string to inject (empty = inject nothing)."""
    from ambiguity_scan import scan_ambiguity
    r = scan_ambiguity(prompt)
    rec = r["recommendation"]
    grill = r.get("grill_suggested")
    lines: list[str] = []
    if rec == "clarify":
        # Tighter batch: ask the PRESENCE-signal questions (real ambiguity), not the
        # absence-signal boilerplate. clarify always has ≥1 presence; fall back to all.
        from ambiguity_scan import _PRESENCE_SIGNALS
        names = [n for n in r["fired"] if n in _PRESENCE_SIGNALS] or r["fired"]
        qs = "; ".join(r["signals"][n]["question"] for n in names if n in r["signals"])
        lines.append(
            "[clarity] 이 요청은 모호성 신호가 높습니다(recommendation=clarify). "
            "진행 전 핵심만 batch 로 확인하세요: " + qs)
    elif rec == "advisory":
        lines.append(
            "[clarity] 경미한 모호성(advisory) — 질문 대신 잡은 가정 1~2줄을 명시하고 진행하세요.")
    # proceed → nothing
    if grill:
        lines.append(
            "[clarity] 큰 변화 신호(" + ", ".join(map(str, r.get("scale_signals", [])))
            + ") — 깊은 탐색이 필요하면 /grill(heavy mode) 제안을 고려하세요.")
    if _needs_reporting_reminder(prompt):
        lines.append(
            "[reporting] 작업 완료/결과/상태를 Owner/CEO에게 보고하는 최종 응답은 "
            "BRIEF/PLAN 형식으로 작성하고 첫 줄을 반드시 \"Bottom Line: ...\"으로 "
            "시작하세요. 자유형 산문으로 마무리하지 마세요.")
    try:
        from role_mentions import render_context as render_role_context
        role_context = render_role_context(prompt)
        if role_context:
            lines.append(role_context)
    except Exception:
        pass
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        prompt = _read_prompt(argv)
        if prompt.strip():
            out = render(prompt)
            if out:
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": out,
                    }
                }, ensure_ascii=True))
                return 0
    except Exception:
        pass  # best-effort: never disrupt the session
    print("{}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
