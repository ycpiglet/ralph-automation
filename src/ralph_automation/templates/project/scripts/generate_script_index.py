#!/usr/bin/env python3
"""Generate scripts/INDEX.md (TASK-248).

The repo intentionally keeps scripts flat for now. This index gives agents a
cheap map from intent to entrypoint without moving files or inventing a second
source of truth.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
INDEX_PATH = SCRIPTS_DIR / "INDEX.md"


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    patterns: tuple[str, ...]
    note: str


CATEGORIES: tuple[Category, ...] = (
    Category("agent-runtime", "Agent Runtime / Collaboration", (
        "agent_", "subagent_", "collab_", "cycle_gate", "qa_negotiation",
    ), "Worker loops, session panes, collaboration gates, subagent evidence."),
    Category("task-backlog", "TASK / Backlog / Views", (
        "task_", "query_tasks", "build_task_index", "validate_task_schema",
        "generate_views", "backlog_sweep",
    ), "TASK frontmatter, generated backlog views, and task events."),
    Category("schedule-automation", "Schedule / Auto Runner / Secretary", (
        "schedule", "auto_runner", "secretary", "macro_detect",
    ), "R1/R2 schedule registry, dry-run runners, digest, and macro proposals."),
    Category("checks", "Checks / Quality Gates", (
        "check_", "doc_health", "doc_steward_due", "scribe_due", "beta_tester_due",
        "precommit", "env_check", "now",
    ), "Read-only validation, due checks, environment and timestamp helpers."),
    Category("tests", "Tests", ("test_",), "Pytest and smoke-test entrypoints."),
    Category("providers", "Providers / LLM Backends", (
        "verify_sdk_backend", "claude_cli_probe", "providers/",
    ), "Provider adapters live under scripts/providers plus targeted verifiers."),
    Category("deploy-data", "Deploy / Data / Assets", (
        "deploy", "migrate", "sync_docs", "generate_manifest", "gen_content",
        "create_buckets", "get_preview_url", "apply_schema",
    ), "Deployment checks, migrations, doc sync, and asset/data helpers."),
    Category("reports-retro", "Reports / Retro / Seminar", (
        "save_report", "query_reports", "generate_report", "agent_retro",
        "agent_seminar", "promote_retro_forward", "compound_metrics",
    ), "Report storage, retrospective, seminar, and feed-forward tools."),
)

ENTRYPOINT_NOTES = {
    "backlog_sweep.py": "First command for backlog/open-work reconciliation.",
    "generate_views.py": "Regenerates BACKLOG.md and TASK views from frontmatter.",
    "check_agent_docs.py": "Strict operating-doc validator; run before closure.",
    "cycle_gate.py": "Classifies current diff and required collaboration roles.",
    "install_hooks.py": "Installs local hooks and slash command wrappers; local settings remain Owner-gated.",
    "schedule_task.py": "OS scheduler status/register wrapper; register/run are R3 boundaries.",
    "local_schedule_daemon.py": "User-session scheduler fallback when Windows Task Scheduler returns 255.",
    "claude_cli_probe.py": "Checks Claude Code CLI, pane/scheduler readiness, and optional live-smoke gate.",
    "task_api.py": "Structured task get/query/set-status API.",
    "task_events.py": "Local append-only task event log viewer.",
    "check_repo_structure.py": "Advisory root/runtime/generated/evidence report.",
    "check_skill_structure.py": "Advisory role Skill progressive-disclosure report.",
    "ralph_migration_inventory.py": "Classifies reusable automation-core vs host/product paths before GitHub extraction.",
}


def _script_paths(root: Path = SCRIPTS_DIR) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name != "INDEX.md" and "__pycache__" not in p.parts
    )


def _rel(path: Path, root: Path = SCRIPTS_DIR) -> str:
    return path.relative_to(root).as_posix()


def _category_for(rel: str) -> Category:
    for category in CATEGORIES:
        if any(rel.startswith(pattern) or Path(rel).name.startswith(pattern) for pattern in category.patterns):
            return category
    return Category("misc", "Miscellaneous", (), "Special-purpose helpers not yet assigned to a larger namespace.")


def classify(paths: list[Path]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {category.key: [] for category in CATEGORIES}
    grouped["misc"] = []
    for path in paths:
        rel = _rel(path)
        grouped.setdefault(_category_for(rel).key, []).append(rel)
    return grouped


def _test_for(rel: str, all_names: set[str]) -> str:
    name = Path(rel).name
    if name.startswith("test_"):
        return ""
    stem = Path(name).stem
    candidate = f"test_{stem}.py"
    return candidate if candidate in all_names else ""


def render(paths: list[Path]) -> str:
    grouped = classify(paths)
    all_names = {Path(_rel(path)).name for path in paths}
    category_by_key = {category.key: category for category in CATEGORIES}
    category_by_key["misc"] = Category("misc", "Miscellaneous", (), "Special-purpose helpers not yet assigned to a larger namespace.")

    lines = [
        "# scripts/INDEX.md",
        "",
        "> Generated by `python scripts/generate_script_index.py`. Do not hand-edit unless the generator is changed.",
        "",
        "## High-Signal Entrypoints",
        "",
        "| Script | Use |",
        "|--------|-----|",
    ]
    for script, note in ENTRYPOINT_NOTES.items():
        if script in all_names:
            lines.append(f"| [`{script}`]({script}) | {note} |")
    lines.extend(["", "## Categories", ""])

    for key in [*(category.key for category in CATEGORIES), "misc"]:
        items = grouped.get(key) or []
        if not items:
            continue
        category = category_by_key[key]
        lines.extend([f"### {category.title}", "", category.note, "", "| Script | Test |", "|--------|------|"])
        for rel in items:
            test = _test_for(rel, all_names)
            test_cell = f"[`{test}`]({test})" if test else "-"
            lines.append(f"| [`{rel}`]({rel}) | {test_cell} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate scripts/INDEX.md")
    parser.add_argument("--check", action="store_true", help="Fail if INDEX.md is stale")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rendered = render(_script_paths())
    if args.check:
        current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
        if current != rendered:
            print("scripts/INDEX.md is stale; run `python scripts/generate_script_index.py`.")
            return 1
        print("scripts/INDEX.md is up to date.")
        return 0
    INDEX_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {INDEX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
