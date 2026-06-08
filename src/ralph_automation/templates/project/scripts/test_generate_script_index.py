from pathlib import Path

import scripts.generate_script_index as gsi


def test_classify_groups_backlog_and_tests():
    paths = [
        gsi.SCRIPTS_DIR / "backlog_sweep.py",
        gsi.SCRIPTS_DIR / "test_backlog_sweep.py",
        gsi.SCRIPTS_DIR / "schedule_task.py",
    ]

    grouped = gsi.classify(paths)

    assert "backlog_sweep.py" in grouped["task-backlog"]
    assert "test_backlog_sweep.py" in grouped["tests"]
    assert "schedule_task.py" in grouped["schedule-automation"]


def test_render_includes_entrypoints_and_test_mapping():
    paths = [
        gsi.SCRIPTS_DIR / "check_agent_docs.py",
        gsi.SCRIPTS_DIR / "test_check_agent_docs.py",
        gsi.SCRIPTS_DIR / "task_api.py",
    ]

    rendered = gsi.render(paths)

    assert "## High-Signal Entrypoints" in rendered
    assert "[`check_agent_docs.py`](check_agent_docs.py)" in rendered
    assert "[`test_check_agent_docs.py`](test_check_agent_docs.py)" in rendered
    assert "Structured task get/query/set-status API" in rendered
    assert "](-)" not in rendered


def test_check_mode_detects_stale_index(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "foo.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(gsi, "SCRIPTS_DIR", scripts)
    monkeypatch.setattr(gsi, "INDEX_PATH", scripts / "INDEX.md")

    assert gsi.main(["--check"]) == 1
