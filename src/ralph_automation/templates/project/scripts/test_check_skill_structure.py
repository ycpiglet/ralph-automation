import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_check_skill_structure_runs():
    result = subprocess.run(
        [sys.executable, "scripts/check_skill_structure.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )

    assert result.returncode == 0
    assert "# Skill Structure Report" in result.stdout
    assert "`lead_engineer`" in result.stdout
    assert "`qa`" in result.stdout


def test_check_skill_structure_is_advisory():
    result = subprocess.run(
        [sys.executable, "scripts/check_skill_structure.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )

    assert "## Recommendation" in result.stdout
    assert "Do not force optional resources for every role" in result.stdout
