#!/usr/bin/env python3
"""TASK-149 — COMPOUND 재발 metric 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import compound_metrics as cm  # noqa: E402
import kedb_search as kedb  # noqa: E402


SAMPLE_LOG = """\
# Compound Log

### COMPOUND-014

카테고리: misinterpretation
재발 횟수: 1

#### 상태
적용 완료.

### COMPOUND-015

카테고리: fidelity-violation
재발 횟수: 2

#### 상태
적용 중.

### COMPOUND-016

카테고리: process-omission
재발 횟수: 7

#### 상태
적용 완료. critical.

### COMPOUND-017

카테고리: process-omission
재발 횟수: 1

#### 상태
적용 완료.
"""


def _metrics():
    return cm.compute_metrics(kedb.parse_compounds(SAMPLE_LOG))


def test_total_and_open_closed():
    m = _metrics()
    assert m["total"] == 4
    assert m["closed"] == 3
    assert m["open"] == 1  # COMPOUND-015 적용 중


def test_critical_counts_recurrence_ge_3():
    m = _metrics()
    assert m["critical"] == ["COMPOUND-016"]
    assert m["critical_count"] == 1


def test_repeated_counts_recurrence_gt_1():
    m = _metrics()
    assert set(m["repeated"]) == {"COMPOUND-015", "COMPOUND-016"}
    assert m["repeated_count"] == 2


def test_category_distribution():
    m = _metrics()
    assert m["by_category"]["process-omission"] == 2
    assert m["by_category"]["misinterpretation"] == 1


def test_recurrence_histogram():
    m = _metrics()
    assert m["recurrence_histogram"]["1"] == 2
    assert m["recurrence_histogram"]["7"] == 1


def test_legacy_missing_recurrence_is_zero():
    entries = kedb.parse_compounds("# Log\n\n### COMPOUND-001\n\n발견한 패턴: legacy.\n")
    m = cm.compute_metrics(entries)
    assert m["recurrence_histogram"]["0"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
