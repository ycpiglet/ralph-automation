"""backlog_sweep smoke test — 모든 surface 섹션이 출력되고 자식 인코딩 함정에 죽지 않는지.

근거: COMPOUND-032. 단일 출처 누락 방지 포레싱 함수가 실제로 전 surface 를 집계하는지 고정.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backlog_sweep.py"


def _run():
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_exits_zero_advisory():
    # advisory — 항상 0 (실패해도 sweep 자체는 죽지 않음)
    assert _run().returncode == 0


def test_aggregates_all_surfaces():
    out = _run().stdout
    # 7 surface 가 모두 한 화면에 — 단일 출처로 좁혀지지 않았는지
    for marker in ("[1]", "[2]", "[3]", "[4]", "[5]", "[6]", "[7]"):
        assert marker in out, f"surface {marker} 누락 — 단일 출처로 좁혀짐"
    # due-check 와 메모리 게이트(과거 누락 항목)가 실제로 노출되는지
    assert "scribe_due" in out
    assert "beta_tester_due" in out
    assert "MEMORY.md" in out
    # 단일 repo-canonical 포인터를 가리키는지 (AUDIT-2026-06-04-002)
    assert "BACKLOG.md" in out


def test_no_unicode_crash():
    # 자식이 cp949 로 흘려도 _readerthread 가 죽지 않아야(Windows 함정)
    r = _run()
    assert "UnicodeDecodeError" not in r.stderr
    assert "열린 TASK" in r.stdout


def test_json_api():
    # 작업목록 API — 구조화 JSON 출력(MCP·스케줄러·HTTP 가 소비)
    import json
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert r.returncode == 0
    data = json.loads(r.stdout)
    for key in ("pointer", "open_tasks", "due_checks", "doc_health", "prose_pointers"):
        assert key in data, f"JSON 키 {key} 누락"
    assert isinstance(data["open_tasks"], list)
    # 열린 작업은 우선순위·상태·owner 를 담아야(소비자가 픽 가능)
    if data["open_tasks"]:
        t = data["open_tasks"][0]
        assert {"id", "status", "priority", "owner"} <= set(t)
