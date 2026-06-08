"""schedule.py CRUD round-trip + validation 테스트 (TASK-224).

PyYAML 비의존 손수 파싱/직렬화가 round-trip 무결한지, validation 이 잘못된 엔트리를 막는지.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_schedule", ROOT / "scripts" / "schedule.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(**kw):
    base = {"id": "x", "cron": "0 9 * * *", "selector": "digest",
            "mode": "notify", "budget": 20000, "enabled": False}
    base.update(kw)
    return base


def test_roundtrip_preserves_entries(tmp_path):
    sm = _load()
    p = tmp_path / "SCHEDULE.yml"
    p.write_text("# header comment\nschedules: []\n", encoding="utf-8")
    entries = [_entry(id="daily-digest"), _entry(id="weekday-maint", cron="0 8 * * 1-5",
                                                 selector="maintenance", mode="pr", enabled=True)]
    sm.write_schedules(entries, p)
    back = sm.read_schedules(p)
    assert back == entries  # exact round-trip (types preserved: int budget, bool enabled)


def test_empty_roundtrip(tmp_path):
    sm = _load()
    p = tmp_path / "SCHEDULE.yml"
    p.write_text("# header\nschedules: []\n", encoding="utf-8")
    sm.write_schedules([], p)
    assert sm.read_schedules(p) == []
    assert "schedules: []" in p.read_text(encoding="utf-8")


def test_header_preserved_on_write(tmp_path):
    sm = _load()
    p = tmp_path / "SCHEDULE.yml"
    p.write_text("# important header\n# line two\nschedules: []\n", encoding="utf-8")
    sm.write_schedules([_entry()], p)
    text = p.read_text(encoding="utf-8")
    assert "# important header" in text and "# line two" in text


def test_validate_rejects_bad_entries():
    sm = _load()
    assert sm.validate(_entry()) == []
    assert sm.validate(_entry(mode="bogus"))           # invalid mode
    assert sm.validate(_entry(budget="20k"))           # non-int budget
    assert sm.validate(_entry(cron="0 9 *"))           # not 5 fields
    assert sm.validate(_entry(id=""))                  # missing id


def test_types_preserved_through_file(tmp_path):
    sm = _load()
    p = tmp_path / "SCHEDULE.yml"
    p.write_text("schedules: []\n", encoding="utf-8")
    sm.write_schedules([_entry(budget=5000, enabled=True)], p)
    e = sm.read_schedules(p)[0]
    assert e["budget"] == 5000 and isinstance(e["budget"], int)
    assert e["enabled"] is True
