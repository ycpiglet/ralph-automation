"""TASK-231 — TASK frontmatter 스키마 검증 테스트."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_vts", ROOT / "scripts" / "validate_task_schema.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vts = _load()
SCHEMA = vts.load_schema()


def _fm(**kw):
    base = {"type": "task", "id": "TASK-999", "status": "대기", "owner": "Lead Engineer",
            "priority": "High", "difficulty": "중", "est_hours": "3", "est_tokens": "35000",
            "tags": ["x"], "created": "2026-06-05", "created_at": "2026-06-05T00:00:00+09:00"}
    base.update(kw)
    return base


def test_valid_passes():
    assert vts.validate_frontmatter(_fm(), SCHEMA) == []


def test_missing_required_status():
    fm = _fm()
    del fm["status"]
    assert any("status" in e for e in vts.validate_frontmatter(fm, SCHEMA))


def test_bad_status_enum():
    errs = vts.validate_frontmatter(_fm(status="진행중"), SCHEMA)  # 공백 없음 = 잘못
    assert any("status" in e and "enum" in e for e in errs)


def test_bad_priority_enum():
    assert any("priority" in e for e in vts.validate_frontmatter(_fm(priority="높음"), SCHEMA))


def test_bad_id_pattern():
    assert any("id" in e for e in vts.validate_frontmatter(_fm(id="T-1"), SCHEMA))


def test_type_const_violation():
    assert any("type" in e for e in vts.validate_frontmatter(_fm(type="story"), SCHEMA))


def test_est_tokens_non_integer():
    assert any("est_tokens" in e for e in vts.validate_frontmatter(_fm(est_tokens="lots"), SCHEMA))


def test_est_tokens_string_int_ok():
    assert vts.validate_frontmatter(_fm(est_tokens="35000"), SCHEMA) == []


def test_est_hours_half_ok():
    # frontmatter 숫자는 문자열 — "0.5" 같은 number 허용
    assert vts.validate_frontmatter(_fm(est_hours="0.5"), SCHEMA) == []


def test_tags_must_be_array():
    assert any("tags" in e for e in vts.validate_frontmatter(_fm(tags="x"), SCHEMA))


def test_created_at_pattern():
    assert any("created_at" in e for e in vts.validate_frontmatter(_fm(created_at="2026/06/05"), SCHEMA))


def test_all_real_tasks_pass():
    # 회귀 가드: 실제 TASK 전부 스키마 통과(스키마=현실 계약, COMPOUND-030)
    assert vts.validate_all() == {}
