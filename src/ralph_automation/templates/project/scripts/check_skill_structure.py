"""Report role SKILL.md progressive-disclosure structure.

This is advisory for now. It checks whether registered role directories are
ready to split large or fragile instructions into GOTCHAS, references,
troubleshooting, scripts, and assets without forcing optional files.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
MAX_SKILL_LINES = 500
OPTIONAL_FILES = ("GOTCHAS.md", "troubleshooting.md")
OPTIONAL_DIRS = ("references", "scripts", "assets")


def role_dirs() -> list[Path]:
    return sorted(p for p in AGENTS.iterdir() if (p / "SKILL.md").exists())


def skill_line_count(role: Path) -> int:
    return len((role / "SKILL.md").read_text(encoding="utf-8").splitlines())


def linked_from_skill(role: Path, name: str) -> bool:
    text = (role / "SKILL.md").read_text(encoding="utf-8")
    return name in text


def render() -> str:
    rows: list[str] = []
    warnings: list[str] = []
    for role in role_dirs():
        lines = skill_line_count(role)
        present = [name for name in OPTIONAL_FILES if (role / name).exists()]
        present += [f"{name}/" for name in OPTIONAL_DIRS if (role / name).is_dir()]
        unlinked = [name for name in OPTIONAL_FILES if (role / name).exists() and not linked_from_skill(role, name)]
        unlinked += [f"{name}/" for name in OPTIONAL_DIRS if (role / name).is_dir() and not linked_from_skill(role, name)]
        if lines > MAX_SKILL_LINES:
            warnings.append(f"{role.name}: SKILL.md has {lines} lines (> {MAX_SKILL_LINES})")
        if unlinked:
            warnings.append(f"{role.name}: resource exists but is not linked from SKILL.md: {', '.join(unlinked)}")
        rows.append(f"| `{role.name}` | {lines} | {', '.join(present) if present else '-'} | {', '.join(unlinked) if unlinked else '-'} |")

    out = [
        "# Skill Structure Report",
        "",
        "| Role | SKILL.md lines | Optional resources present | Existing resources not linked from SKILL.md |",
        "|------|----------------:|----------------------------|--------------------------------------------|",
        *rows,
        "",
        "## Warnings",
        "",
    ]
    if warnings:
        out.extend(f"- {warning}" for warning in warnings)
    else:
        out.append("- none")
    out.extend(
        [
            "",
            "## Recommendation",
            "",
            "- Keep `SKILL.md` concise and use it as navigation.",
            "- Move details to `GOTCHAS.md`, `troubleshooting.md`, `references/`, `scripts/`, or `assets/` only when they reduce repeated context load.",
            "- Do not force optional resources for every role; add them when there is real repeated use.",
        ]
    )
    return "\n".join(out)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
