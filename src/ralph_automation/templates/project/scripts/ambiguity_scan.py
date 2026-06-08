#!/usr/bin/env python3
"""Mechanical ambiguity scanner for the requirements_interviewer role (TASK-216).

Implements the *deterministic* half of the deep-interview protocol: a lexical +
structural scan of a request that flags ambiguity signals and maps each to a
clarifying-question category. The adaptive half (which question to ask next,
when to stop) lives in the role's SKILL.md and is LLM-driven — this script is
the high-recall front end that the interviewer (or any agent) runs at INTAKE and
after each answer round so no signal is silently skipped.

Design (from EVIDENCE-2026-06-03-001 research synthesis):
  - High RECALL over precision — better to over-flag than miss ambiguity
    (Berry: ambiguous informal text becomes "unambiguously wrong" once formalized).
  - Bilingual (Korean + English) — this is a Korean-operated project.
  - Pure: scan(text) -> dict, no I/O, fully testable.

Usage:
  python scripts/ambiguity_scan.py "로그인 빠르게 개선해줘"
  python scripts/ambiguity_scan.py --format json "make the dashboard better"
  echo "..." | python scripts/ambiguity_scan.py -
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# --- signal vocabularies (bilingual) ---------------------------------------

# 1) Vague quantifiers / weak words — untestable without a number/criterion.
_VAGUE_WORDS = [
    # English
    "fast", "faster", "fast enough", "slow", "scalable", "robust", "user-friendly",
    "user friendly", "intuitive", "simple", "easy", "better", "improve", "improved",
    "nice", "clean", "modern", "flexible", "efficient", "performant", "soon",
    "some", "most", "several", "many", "a few", "a lot", "various", "etc",
    "and so on", "as needed", "appropriately", "properly", "reasonable",
    # Korean
    "빠르게", "빠른", "빨리", "느린", "느리", "개선", "좋게", "좋은", "더 나은", "나은",
    "깔끔", "예쁘", "이쁘", "직관", "간단", "쉽게", "편하게", "편리", "유연", "효율",
    "최적화", "곧", "조금", "약간", "적당", "대충", "알아서", "잘", "좀", "몇몇", "여러",
    "등등", "기타", "필요하면", "필요시", "원활",
]

# 2) Acceptance-criteria markers — their PRESENCE means the request is testable.
_ACCEPTANCE_MARKERS = [
    "done when", "acceptance", "criteria", "정확히", "이내", "이하", "이상", "초과",
    "미만", "완료 기준", "기준은", "테스트", "검증", "측정", "성공 조건", "기대 결과",
    "should equal", "must be", "at least", "at most", "within", "no more than",
]

# 3) Solution/feature verbs — a stated solution without a goal needs laddering-up.
_SOLUTION_VERBS = [
    "추가", "만들", "구현", "넣어", "붙여", "달아", "바꿔", "교체", "삭제", "제거",
    "add", "build", "create", "implement", "make", "replace", "remove", "delete",
    "put a", "change the",
]
# Goal markers — if present, the goal behind the solution is (partly) given.
_GOAL_MARKERS = [
    "왜냐", "때문", "위해", "위한", "목표", "목적", "하려고", "이유는", "그래야",
    "so that", "because", "in order to", "the goal", "so users", "so we",
]

# 4) Unresolved referents — demonstratives with no clear antecedent.
_REFERENT_WORDS = [
    "이거", "그거", "저거", "이것", "그것", "저것", "여기", "거기", "그 부분", "그쪽",
    "this", "that", "it", "those", "these", "the thing",
]

# 5) Conflicting / untraded goal pairs — desirable properties that trade off.
_CONFLICT_PAIRS = [
    # cheap-side uses 저비용/비용 절감, not bare 비용 (which also matches "비용 들여서" = the opposite).
    (("빠르", "fast", "빨리"), ("싸게", "저렴", "cheap", "저비용", "비용 절감")),
    (("싸게", "저렴", "cheap"), ("고가용", "안정", "highly available", "robust", "무중단")),
    (("간단", "simple", "쉽게"), ("강력", "powerful", "유연", "flexible", "모든 기능")),
    (("빠르", "fast"), ("정확", "accurate", "정밀")),
]

# 6) Scope markers — their PRESENCE means scope is at least partly bounded.
# NOTE: bare requirement modals "must"/"should" are NOT scope markers — including
# them silently cleared the absence signal on ordinary requirements (false negative,
# wrong direction vs the high-recall goal). Keep only genuine scope language.
_SCOPE_MARKERS = [
    "범위", "스코프", "제외", "포함", "까지만", "안 함", "하지 않", "out of scope",
    "in scope", "scope", "only", "만 ", "won't", "wont",
    "이번엔", "이번에는", "우선", "일단",
]

# Scale signals — markers that the request is a LARGE change (new idea / system
# structure / architecture / broad scope). Their presence suggests the HEAVY
# /grill mode (intensive, many focused questions) rather than the light everyday
# check. Orthogonal to ambiguity: a clear large request still benefits from grill.
_SCALE_SIGNALS = [
    # Korean — kept specific; dropped over-broad single tokens (구조/통합/설계/전체적)
    # that false-fire inside 구조체·통합검색·설계도 etc. (reviewer M1).
    "시스템", "아키텍처", "아키텍쳐", "재설계", "리팩터", "리팩토링", "리팩터링",
    "마이그레이션", "전면 개편", "전면 재", "새 기능", "신규 기능", "새로운 기능",
    "새 역할", "신규 역할", "플랫폼", "파이프라인", "워크플로", "프레임워크",
    # English
    "architecture", "redesign", "refactor", "migration", "migrate", "new system",
    "new feature", "new role", "platform", "framework", "overhaul", "end-to-end",
    "pipeline", "workflow", "rearchitect", "from scratch", "greenfield",
]


def _hits(text_low: str, vocab) -> list[str]:
    """Find vocab terms in text. ASCII terms match on WORD BOUNDARIES (so "some"
    doesn't fire inside "awesome", "it" inside "edit", "only" inside "commonly");
    Korean terms use substring (no regex word boundary between Hangul + \\W)."""
    found = []
    for w in vocab:
        if w.isascii():
            if re.search(r"\b" + re.escape(w) + r"\b", text_low):
                found.append(w)
        elif w in text_low:
            found.append(w)
    return found


def _has(text_low: str, vocab) -> bool:
    return any(w in text_low for w in vocab)


# Socratic question category suggested per signal (EVIDENCE-2026-06-03-001).
_SIGNAL_QUESTION = {
    "vague_quantifiers": "clarification — define the vague term with a number/percentile or concrete example",
    "missing_acceptance_criteria": "implications — state an observable 'done when ___' pass/fail condition",
    "solution_as_requirement": "probe-assumptions (ladder-up) — what goal/job does this solve? (JTBD, COMPOUND-029)",
    "unresolved_referents": "clarification — what exactly does the referent point to?",
    "conflicting_goals": "viewpoints — these goals trade off; which ranks higher?",
    "unstated_scope": "question-the-question — what is explicitly in / out of scope this round?",
}

# PRESENCE signals are positive evidence of ambiguity (a vague word, a goalless
# solution, an unresolved referent, a goal conflict actually appears). ABSENCE
# signals (missing_acceptance_criteria, unstated_scope) fire on almost any short
# request, so they don't, alone, justify interrupting an autonomous flow.
_PRESENCE_SIGNALS = {
    "vague_quantifiers", "solution_as_requirement",
    "unresolved_referents", "conflicting_goals",
}


def scan_ambiguity(text: str) -> dict:
    """Scan a request for ambiguity signals. Pure: returns a structured dict.

    Returns {signals: {name: {fired, evidence, question}}, fired: [names],
    clarity_score: 0..1 (1 = no signal fired), summary: str}.
    """
    raw = (text or "").strip()
    low = raw.lower()
    signals: dict = {}

    def add(name: str, fired: bool, evidence) -> None:
        signals[name] = {
            "fired": bool(fired),
            "evidence": evidence,
            "question": _SIGNAL_QUESTION[name],
        }

    # 1) vague quantifiers
    vague = _hits(low, _VAGUE_WORDS)
    add("vague_quantifiers", bool(vague), vague)

    # 2) missing acceptance criteria — no acceptance marker AND no digit
    has_accept = _has(low, _ACCEPTANCE_MARKERS)
    has_number = bool(re.search(r"\d", low))
    add("missing_acceptance_criteria", not (has_accept or has_number),
        {"acceptance_marker": has_accept, "has_number": has_number})

    # 3) solution stated without goal
    sol = _hits(low, _SOLUTION_VERBS)
    has_goal = _has(low, _GOAL_MARKERS)
    add("solution_as_requirement", bool(sol) and not has_goal,
        {"solution_verbs": sol, "goal_marker": has_goal})

    # 4) unresolved referents
    refs = _hits(low, _REFERENT_WORDS)
    add("unresolved_referents", bool(refs), refs)

    # 5) conflicting goals — both sides of a trade-off pair present, no explicit rank
    conflicts = []
    for a_terms, b_terms in _CONFLICT_PAIRS:
        if _has(low, a_terms) and _has(low, b_terms):
            conflicts.append({"a": [t for t in a_terms if t in low],
                              "b": [t for t in b_terms if t in low]})
    add("conflicting_goals", bool(conflicts), conflicts)

    # 6) unstated scope
    add("unstated_scope", not _has(low, _SCOPE_MARKERS), None)

    fired = [n for n, s in signals.items() if s["fired"]]
    total = len(signals)
    clarity_score = round(1 - len(fired) / total, 3) if total else 1.0

    # Recommendation gating: PRESENCE signals (vague/solution/referent/conflict) are
    # positive evidence of ambiguity; ABSENCE signals (missing acceptance / unstated
    # scope) fire on nearly every short request, so absence ALONE must NOT demand an
    # interview — that was the false-positive that made greetings read as "clarify".
    p = sum(1 for n in fired if n in _PRESENCE_SIGNALS)
    if p == 0:
        recommendation = "proceed"
    elif p >= 2 or len(fired) >= 3:
        recommendation = "clarify"
    else:
        recommendation = "advisory"
    rec_msg = {
        "proceed": "no actionable ambiguity — proceed (absence-only signals are weak).",
        "advisory": "minor ambiguity — clarify only if low-confidence.",
        "clarify": "high ambiguity — run the interview (/grill) before planning.",
    }[recommendation]

    # Scale → suggest HEAVY /grill mode (large change: new idea/system/architecture).
    scale_hits = _hits(low, _SCALE_SIGNALS)
    grill_suggested = bool(scale_hits)
    grill_msg = (
        f" LARGE change ({', '.join(map(str, scale_hits))}) — heavy /grill suggested."
        if grill_suggested else ""
    )
    summary = (
        f"{len(fired)}/{total} signal(s) fired (presence={p}); "
        f"clarity_score={clarity_score}; recommendation={recommendation} — {rec_msg}{grill_msg}"
    )
    return {
        "signals": signals,
        "fired": fired,
        "presence_count": p,
        "clarity_score": clarity_score,
        "recommendation": recommendation,
        "grill_suggested": grill_suggested,
        "scale_signals": scale_hits,
        "summary": summary,
    }


def _format_human(result: dict) -> str:
    lines = [f"[ambiguity_scan] {result['summary']}"]
    for name in result["fired"]:
        s = result["signals"][name]
        ev = s["evidence"]
        ev_str = ""
        if isinstance(ev, list) and ev:
            ev_str = f" ({', '.join(map(str, ev))})"
        lines.append(f"  - {name}{ev_str}")
        lines.append(f"      → ask: {s['question']}")
    if not result["fired"]:
        lines.append("  (request is mechanically clear; adaptive review still advised)")
    elif result["recommendation"] == "proceed":
        lines.append("  (only absence-based signals — not enough to interrupt; proceed)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mechanical ambiguity scanner (requirements_interviewer)")
    ap.add_argument("text", nargs="?", default=None,
                    help="request text to scan; use '-' to read stdin")
    ap.add_argument("--format", choices=["human", "json"], default="human")
    args = ap.parse_args(argv)

    if args.text is None:
        ap.error("provide request text (or '-' for stdin)")
    text = sys.stdin.read() if args.text == "-" else args.text

    result = scan_ambiguity(text)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_human(result))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
