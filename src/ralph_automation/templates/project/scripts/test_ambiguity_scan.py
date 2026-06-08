"""Unit tests for the mechanical ambiguity scanner (TASK-216).

Deterministic, no I/O — asserts each ambiguity signal fires on a positive case
and stays quiet on a clear one, plus the clarity_score / fired aggregation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ambiguity_scan import scan_ambiguity  # noqa: E402


def _fired(text):
    return set(scan_ambiguity(text)["fired"])


# ---- individual signals ----

def test_vague_quantifiers_fire_korean_and_english():
    assert "vague_quantifiers" in _fired("make it faster")
    assert "vague_quantifiers" in _fired("좀 더 좋게 개선해줘")


def test_missing_acceptance_criteria_fires_without_number_or_marker():
    assert "missing_acceptance_criteria" in _fired("improve the page")
    # a concrete number or acceptance marker clears it
    assert "missing_acceptance_criteria" not in _fired("keep p95 under 300ms")
    assert "missing_acceptance_criteria" not in _fired("done when the test passes")


def test_solution_as_requirement_fires_without_goal():
    assert "solution_as_requirement" in _fired("add a button")
    assert "solution_as_requirement" in _fired("다크모드 버튼 추가")
    # a goal marker ladders it up → not flagged
    assert "solution_as_requirement" not in _fired("add a button so that users can undo")
    assert "solution_as_requirement" not in _fired("되돌리기 위해 버튼 추가")


def test_unresolved_referents_fire():
    assert "unresolved_referents" in _fired("그거 고쳐줘")
    assert "unresolved_referents" in _fired("fix this please")


def test_conflicting_goals_fire_when_both_sides_present():
    assert "conflicting_goals" in _fired("빠르고 싸게 만들어줘")
    assert "conflicting_goals" in _fired("make it cheap but highly available")
    assert "conflicting_goals" not in _fired("make it cheap")


def test_unstated_scope_fires_without_scope_marker():
    assert "unstated_scope" in _fired("improve login")
    assert "unstated_scope" not in _fired("이번엔 로그인만, 회원가입은 제외")


# ---- aggregation ----

def test_clear_request_fires_nothing_and_scores_one():
    r = scan_ambiguity(
        "Reduce /login p95 latency to under 300ms; done when the prod dashboard "
        "shows p95 < 300ms for 24h. In scope: login only, signup out of scope."
    )
    assert r["fired"] == []
    assert r["clarity_score"] == 1.0
    assert "proceed" in r["summary"]


def test_ambiguous_request_lowers_score_and_lists_questions():
    r = scan_ambiguity("좀 빠르게 개선해줘")
    assert r["clarity_score"] < 1.0
    assert len(r["fired"]) >= 2
    # every fired signal carries a suggested question
    for name in r["fired"]:
        assert r["signals"][name]["question"]


def test_empty_text_is_maximally_ambiguous_but_safe():
    r = scan_ambiguity("")
    # no crash; empty request has no acceptance criteria / no scope → some signals fire
    assert isinstance(r["fired"], list)
    assert 0.0 <= r["clarity_score"] <= 1.0


def test_scan_is_pure_and_repeatable():
    t = "add a fast thing"
    assert scan_ambiguity(t) == scan_ambiguity(t)


# ---- recommendation gating (reviewer/skeptic fix: absence-only must not interrupt) ----

def test_greeting_and_trivial_requests_recommend_proceed():
    # absence-only signals (no presence evidence) → must NOT demand an interview
    for t in ["안녕하세요", "DB 연결 테스트 실행", "Reset the user password for account X"]:
        r = scan_ambiguity(t)
        assert r["recommendation"] == "proceed", (t, r["fired"])


def test_presence_signal_request_recommends_clarify():
    assert scan_ambiguity("좀 빠르게 개선해줘")["recommendation"] == "clarify"
    assert scan_ambiguity("add a button")["recommendation"] == "clarify"


def test_word_boundary_no_false_fire_inside_words():
    # 'some'/'most'/'many' must not fire inside awesome/almost/Germany;
    # 'it' must not fire inside editing/committing/config.
    r = scan_ambiguity("awesome almost commonly used in Germany; editing and committing config")
    assert "vague_quantifiers" not in r["fired"]
    assert "unresolved_referents" not in r["fired"]


def test_scope_not_cleared_by_bare_modal_verbs():
    # 'should'/'must' are requirement modals, not scope markers
    assert "unstated_scope" in _fired("the form should validate email")
    assert "unstated_scope" in _fired("users must log in")


def test_bare_solution_verb_fires_without_article():
    assert "solution_as_requirement" in _fired("add validation")


# ---- scale detection → heavy /grill suggestion (TASK-217) ----

def test_scale_signal_suggests_grill():
    for t in ["새 시스템 구조를 설계하자", "let's rearchitect the auth pipeline",
              "전면 리팩토링", "build a new platform"]:
        r = scan_ambiguity(t)
        assert r["grill_suggested"] is True, (t, r["scale_signals"])


def test_no_scale_signal_no_grill():
    r = scan_ambiguity("fix the login button color")
    assert r["grill_suggested"] is False
    assert r["scale_signals"] == []


def test_large_but_clear_request_still_suggests_grill():
    # scale is orthogonal to ambiguity: a precise architecture request still grills
    r = scan_ambiguity("Redesign the dispatch architecture; done when all 500 tests pass. scope: providers only")
    assert r["grill_suggested"] is True
    assert r["recommendation"] in ("proceed", "advisory", "clarify")
