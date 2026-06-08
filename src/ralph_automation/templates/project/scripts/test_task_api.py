"""TASK-233 — 구조화 task read API 테스트 (read 부분)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_tapi", ROOT / "scripts" / "task_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tapi = _load()


def test_get_known_task():
    t = tapi.get("TASK-231")
    assert t is not None and t["status"] == "완료" and t["id"] == "TASK-231"


def test_get_missing_returns_none():
    assert tapi.get("TASK-999999") is None


def test_query_by_status():
    res = tapi.query(status="완료")
    assert res and all(o["status"] == "완료" for o in res)


def test_query_by_tag_membership():
    res = tapi.query(tag="task-model")
    ids = {o["id"] for o in res}
    assert {"TASK-231", "TASK-232"} <= ids


def test_query_combined_and():
    res = tapi.query(status="대기", tag="task-model")
    assert all(o["status"] == "대기" and "task-model" in o.get("tags", []) for o in res)


def test_query_no_filter_returns_all():
    assert len(tapi.query()) == len(tapi.load_tasks()) > 0


# ---- write-through (status) ----

def test_replace_frontmatter_field_only_touches_frontmatter():
    text = "---\ntype: task\nstatus: 대기\n---\n[작업 지시]\n상태: 대기\n"
    out = tapi._replace_frontmatter_field(text, "status", "완료")
    assert "status: 완료" in out
    assert "상태: 대기" in out  # body 불침범


def test_replace_body_status():
    text = "---\nstatus: 대기\n---\n[작업 지시]\n상태: 대기\n본문"
    out = tapi._replace_body_status(text, "완료")
    assert "상태: 완료" in out and "status: 대기" in out  # frontmatter 불침범


def test_update_index_status(tmp_path):
    idx = tmp_path / "INDEX.md"
    idx.write_text("| [TASK-231](TASK-231-x.md) | 진행 중 | Lead Engineer | m | — | 요약 |\n", encoding="utf-8")
    assert tapi._update_index_status("TASK-231", "완료", index_md=idx) is True
    assert "| 완료 | Lead Engineer |" in idx.read_text(encoding="utf-8")


def test_update_status_rejects_bad_enum():
    assert tapi.update_status("TASK-231", "진행중")["ok"] is False  # 공백 없음=잘못


def test_update_status_rejects_unknown_task():
    assert tapi.update_status("TASK-999999", "대기")["ok"] is False
