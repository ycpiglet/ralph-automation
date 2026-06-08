"""TASK-234 — task 변경 이벤트 로그 테스트."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_tev", ROOT / "scripts" / "task_events.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tev = _load()


def test_append_and_read_roundtrip(tmp_path):
    p = tmp_path / "ev.jsonl"
    e1 = tev.append_event("update", "TASK-1", {"status": {"from": "대기", "to": "진행 중"}}, path=p)
    e2 = tev.append_event("update", "TASK-2", {"status": {"from": "대기", "to": "완료"}}, actor="x", path=p)
    evs = tev.read_events(p)
    assert len(evs) == 2
    assert evs[0]["task_id"] == "TASK-1" and evs[1]["actor"] == "x"
    assert e1["seq"] == 1 and e2["seq"] == 2  # 단조 증가


def test_event_shape(tmp_path):
    p = tmp_path / "ev.jsonl"
    e = tev.append_event("update", "TASK-9", {"status": {"from": "a", "to": "b"}}, path=p)
    for k in ("seq", "ts", "action", "task_id", "fields", "actor"):
        assert k in e
    assert "T" in e["ts"]  # ISO8601


def test_seq_continues_across_appends(tmp_path):
    p = tmp_path / "ev.jsonl"
    for i in range(3):
        tev.append_event("update", f"TASK-{i}", path=p)
    assert tev._next_seq(p) == 4
    assert [e["seq"] for e in tev.read_events(p)] == [1, 2, 3]


def test_events_for_filter(tmp_path):
    p = tmp_path / "ev.jsonl"
    tev.append_event("update", "TASK-A", path=p)
    tev.append_event("update", "TASK-B", path=p)
    tev.append_event("update", "TASK-A", path=p)
    assert len(tev.events_for("TASK-A", path=p)) == 2


def test_read_missing_returns_empty(tmp_path):
    assert tev.read_events(tmp_path / "nope.jsonl") == []
