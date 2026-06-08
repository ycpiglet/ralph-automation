"""session_start_hook 복도 게시판 주입 고정 (AUDIT-2026-06-04-002).

세션 시작 시 BACKLOG.md(단일 포인터)가 컨텍스트에 주입되고 기존 협업 콕핏도 보존되는지.
어느 세션/PC 든 같은 열린작업을 보게 하는 read-side 강제.
"""
import subprocess
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run():
    return subprocess.run(
        [sys.executable, "scripts/session_start_hook.py"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def _context(stdout: str) -> str:
    payload = json.loads(stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


def test_board_surfaces_backlog_and_preserves_cockpit():
    r = _run()
    assert r.returncode == 0  # advisory — 절대 세션 안 막음
    out = _context(r.stdout)
    assert "복도 게시판" in out
    assert "BACKLOG.md" in out
    assert "협업 콕핏" in out  # 기존 기능 보존(회귀 방지)


def test_board_shows_open_tasks():
    out = _context(_run().stdout)
    # 게시판이 실제 열린작업(보류 게이트 포함)을 담는지
    assert "보류" in out or "대기" in out or "진행 중" in out
    assert "TASK-" in out


def test_board_surfaces_local_schedule_daemon_status():
    out = _context(_run().stdout)
    assert "local schedule daemon" in out
