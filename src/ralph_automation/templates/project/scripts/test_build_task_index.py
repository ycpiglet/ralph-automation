"""TASK-232 — 구조화 task 인덱스 빌더 테스트."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_bti", ROOT / "scripts" / "build_task_index.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bti = _load()


def test_build_returns_id_sorted_objects():
    objs = bti.build()
    ids = [bti._id_num(o["id"]) for o in objs]
    assert ids == sorted(ids)  # 결정적 정렬
    assert objs and all("id" in o and "status" in o for o in objs)
    assert all("_path" in o and "_title" in o for o in objs)  # 파생 메타


def test_index_is_schema_compliant():
    # ②인덱스 전 항목이 ①스키마(TASK-231) 통과 — 계약 연쇄
    assert bti.check_against_schema() == {}


def test_document_shape():
    doc = bti.to_document()
    assert doc["version"] == bti.INDEX_VERSION
    assert doc["count"] == len(doc["tasks"]) > 0


def test_deterministic_output():
    assert bti.to_json() == bti.to_json()  # 동일 입력 → 동일 출력(재생성 안전)


def test_known_task_present():
    objs = bti.build()
    t231 = [o for o in objs if o.get("id") == "TASK-231"]
    assert t231 and t231[0]["status"] == "완료"


def test_write_index(tmp_path):
    import json
    p = bti.write_index(tmp_path / "tasks.index.json")
    assert p.exists()
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["count"] > 0 and isinstance(doc["tasks"], list)
