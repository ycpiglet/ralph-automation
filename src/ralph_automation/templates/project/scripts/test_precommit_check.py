#!/usr/bin/env python3
"""TASK-147 — pre-commit 게이트 부산물 검출 테스트 (COMPOUND-015)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import precommit_check as pc  # noqa: E402


def test_blocks_tmp_directory_artifact():
    blocked = pc.find_blocked_artifacts(["tmp_blocks/b2.js", "public/app.js"])
    assert "tmp_blocks/b2.js" in blocked
    assert "public/app.js" not in blocked


def test_blocks_underscore_tmp_file():
    blocked = pc.find_blocked_artifacts([".brief_body_tmp.md"])
    assert blocked == [".brief_body_tmp.md"]


def test_blocks_dot_tmp_and_tmp_dir():
    blocked = pc.find_blocked_artifacts(["foo.tmp", "tmp/scratch.txt"])
    assert set(blocked) == {"foo.tmp", "tmp/scratch.txt"}


def test_blocks_scratch_and_workspace():
    blocked = pc.find_blocked_artifacts(["scratch_foo/x.py", "_workspace/y.md"])
    assert set(blocked) == {"scratch_foo/x.py", "_workspace/y.md"}


def test_debug_prefix_not_blocked_false_positive_guard():
    blocked = pc.find_blocked_artifacts(["scripts/debug_utils.py"])
    assert blocked == []


def test_clean_paths_pass():
    blocked = pc.find_blocked_artifacts([
        "scripts/kedb_search.py",
        "agents/lead_engineer/CYCLE-025.md",
        "public/data.js",
    ])
    assert blocked == []


def test_normalizes_backslashes():
    blocked = pc.find_blocked_artifacts(["agents\\tmp_x\\file.md"])
    assert blocked == ["agents/tmp_x/file.md"]


def test_main_with_files_blocks(capsys):
    rc = pc.main(["--files", "tmp_blocks/x.js", "--no-views"])
    assert rc == 1
    assert "COMPOUND-015" in capsys.readouterr().err


def test_main_with_clean_files_passes():
    rc = pc.main(["--files", "scripts/kedb_search.py", "--no-views"])
    assert rc == 0


def test_all_mode_scans_tracked_files_blocks(monkeypatch, capsys):
    monkeypatch.setattr(pc, "tracked_files", lambda: ["scripts/x.py", "tmp_blocks/y.js"])
    rc = pc.main(["--all", "--no-views"])
    assert rc == 1
    assert "COMPOUND-015" in capsys.readouterr().err


def test_all_mode_clean_tracked_passes(monkeypatch):
    monkeypatch.setattr(pc, "tracked_files", lambda: ["scripts/x.py", "public/app.js"])
    rc = pc.main(["--all", "--no-views"])
    assert rc == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
