#!/usr/bin/env python3
"""TASK-150 — KEDB 검색 도구 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import kedb_search as kedb  # noqa: E402


SAMPLE_LOG = """\
# Compound Log

### COMPOUND-015

카테고리: fidelity-violation
재발 횟수: 1

#### 발견한 패턴
git add -A 로 tmp_blocks/ 부산물 commit.

#### 상태
적용 중.

### COMPOUND-016

카테고리: process-omission
재발 횟수: 7

#### 발견한 패턴
save_report 후 VIEW 재생성 누락. CI VIEW-stale 반복.

#### 상태
적용 완료. critical.

### COMPOUND-017

카테고리: process-omission
재발 횟수: 1

#### 발견한 패턴
사용자 부정 신호 즉시 누적 안 함.

#### 상태
적용 완료.
"""


def _entries():
    return kedb.parse_compounds(SAMPLE_LOG)


def test_parse_extracts_fields():
    e = {x["id"]: x for x in _entries()}
    assert e["COMPOUND-016"]["recurrence"] == 7
    assert e["COMPOUND-016"]["category"] == "process-omission"
    assert "VIEW" in e["COMPOUND-016"]["pattern"]


def test_keyword_match_scores_and_sorts():
    rows = kedb.search(_entries(), ["VIEW"], None, False)
    assert rows and rows[0]["id"] == "COMPOUND-016"
    assert rows[0]["score"] >= 1


def test_no_match_returns_empty():
    rows = kedb.search(_entries(), ["nonexistent-xyz"], None, False)
    assert rows == []


def test_category_filter():
    rows = kedb.search(_entries(), [], "fidelity-violation", False)
    assert [r["id"] for r in rows] == ["COMPOUND-015"]


def test_critical_filter_only_recurrence_ge_3():
    rows = kedb.search(_entries(), [], None, True)
    assert [r["id"] for r in rows] == ["COMPOUND-016"]


def test_render_table_no_match_message():
    out = kedb.render_table([], ["zzz"])
    assert "no matching COMPOUND" in out


def test_critical_constant():
    assert kedb.CRITICAL_RECURRENCE == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
