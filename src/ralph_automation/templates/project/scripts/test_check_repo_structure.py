from pathlib import Path

import scripts.check_repo_structure as crs


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_analyze_accepts_known_source_dirs_and_flags_runtime(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "scripts").mkdir()
    _touch(tmp_path / "AGENTS.md")
    _touch(tmp_path / "tasks.index.json")
    _touch(tmp_path / ".tmp-http-8768.out")

    findings = crs.analyze(tmp_path)
    categories = {f.path: f.category for f in findings}

    assert "agents" not in categories
    assert "scripts" not in categories
    assert "AGENTS.md" not in categories
    assert categories["tasks.index.json"] == "generated-runtime-file"
    assert categories[".tmp-http-8768.out"] == "generated-runtime-file"


def test_analyze_flags_root_evidence_and_local_dirs(tmp_path):
    _touch(tmp_path / "cycle-020-mobile-en-dark.png")
    (tmp_path / ".codex").mkdir()
    _touch(tmp_path / ".env")

    findings = crs.analyze(tmp_path)
    categories = {f.path: f.category for f in findings}

    assert categories["cycle-020-mobile-en-dark.png"] == "root-evidence-file"
    assert categories[".codex"] == "local-runtime-dir"
    assert categories[".env"] == "secret-or-local"


def test_render_outputs_markdown_table():
    report = crs.render([crs.Finding("x.txt", "root-evidence-file", "move it")])

    assert "# Repo Root Structure Report" in report
    assert "| `x.txt` | root-evidence-file | move it |" in report
