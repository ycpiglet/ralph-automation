#!/usr/bin/env python3
"""Advisory @role mention parser.

Raw @mentions are routing hints only. They never execute workers, write TASKs,
dispatch subagents, or produce evidence by themselves.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_orchestrator as ao  # noqa: E402

PERSPECTIVE_SUBAGENTS = {"reviewer", "skeptic", "auditor", "strategist", "implementer"}
NON_WORKER = {"owner"}
SECRETARY_PHRASES = (
    "오늘 내가 볼 것",
    "오늘 내가 봐야",
    "내 결정 사항",
    "Owner 결정",
    "owner 결정",
    "비서",
    "digest",
    "스케줄 요약",
)
RECORD_PHRASES = (
    "기록 남겨",
    "message bus",
    "메시지 버스",
    "/call",
    "실제 에이전트 호출",
    "evidence 남겨",
)
MENTION_RE = re.compile(r"(?<![\w./-])@([A-Za-z][A-Za-z0-9_-]*)")


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _normalize_mention(raw: str) -> str | None:
    key = raw.strip().lower().replace("_", "-")
    if key in NON_WORKER:
        return key
    if key in PERSPECTIVE_SUBAGENTS:
        return key
    if key in ao.ROLE_ALIASES:
        return ao.ROLE_ALIASES[key]
    return None


def _secretary_phrase_present(prompt: str) -> bool:
    return any(phrase in prompt for phrase in SECRETARY_PHRASES)


def _record_phrase_present(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(phrase.lower() in lowered for phrase in RECORD_PHRASES)


def analyze(prompt: str) -> dict:
    text = prompt or ""
    raw_mentions = MENTION_RE.findall(text)
    meeting_requested = any(item.strip().lower().replace("_", "-") == "meeting" for item in raw_mentions)
    normalized = [_normalize_mention(item) for item in raw_mentions]
    roles = [item for item in normalized if item]

    triggers: list[str] = []
    if meeting_requested:
        triggers.append("meeting-mention")
    if not roles and _secretary_phrase_present(text):
        roles.append("secretary")
        triggers.append("secretary-phrase")

    worker_roles = _dedupe([
        role for role in roles
        if role not in PERSPECTIVE_SUBAGENTS and role not in NON_WORKER
    ])
    perspective_subagents = _dedupe([
        role for role in roles if role in PERSPECTIVE_SUBAGENTS
    ])
    non_worker = _dedupe([role for role in roles if role in NON_WORKER])
    all_roles = _dedupe(worker_roles + perspective_subagents + non_worker)
    has_signal = bool(all_roles or meeting_requested)

    if not has_signal:
        mode = "none"
    elif _record_phrase_present(text):
        mode = "record-call"
    elif len(all_roles) >= 2 or meeting_requested:
        mode = "meeting-preview"
    else:
        mode = "chat-only"

    notes: list[str] = []
    if "owner" in non_worker:
        notes.append("Owner escalation context only; never dispatch @owner.")
    if mode == "record-call":
        notes.append("explicit execution path required")
    if mode == "meeting-preview":
        notes.append("preview first; do not fan out from raw mentions")
    if "secretary" in worker_roles:
        notes.append("secretary is R1 only: report, reminder, suggestion")

    return {
        "has_signal": has_signal,
        "mode": mode,
        "worker_roles": worker_roles,
        "perspective_subagents": perspective_subagents,
        "non_worker": non_worker,
        "all_roles": all_roles,
        "triggers": triggers,
        "writes_files": False,
        "notes": notes,
    }


def render_context(prompt: str) -> str:
    result = analyze(prompt)
    if not result["has_signal"]:
        return ""
    worker = ",".join(result["worker_roles"]) or "-"
    subagents = ",".join(result["perspective_subagents"]) or "-"
    non_worker = ",".join(result["non_worker"]) or "-"
    notes = "; ".join(result["notes"]) or "raw mentions are hints only"
    return (
        "[role-mention] "
        f"mode={result['mode']} worker_roles={worker} "
        f"perspective_subagents={subagents} non_worker={non_worker}. "
        "Treat @mentions as current-chat role framing unless the user explicitly "
        "asks for /call/message evidence. " + notes
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prompt = " ".join(argv)
    print(render_context(prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
