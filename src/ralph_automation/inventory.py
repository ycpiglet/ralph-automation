from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


CLASSES = ("core", "core-template", "host-state", "product", "local", "review")

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}

LOCAL_PREFIXES = (
    ".agents/",
    ".claude/",
    ".codex/",
    ".playwright-mcp/",
    ".pytest_cache/",
    "schedule_runs/",
)

LOCAL_FILES = {
    ".env",
    ".env.local",
    "tasks.index.json",
    "tasks.events.jsonl",
    "eval_log.jsonl",
}

PRODUCT_PREFIXES = (
    "public/",
    "src/assets/",
    "supabase/",
    "docs/manuals/",
    "docs/vendor_docs/",
    "public/docs/",
)

PRODUCT_SCRIPTS = {
    "scripts/apply_schema.mjs",
    "scripts/check_deployment.py",
    "scripts/create_buckets.mjs",
    "scripts/deploy_edge_function.py",
    "scripts/gen_content_seed.mjs",
    "scripts/generate_manifest.py",
    "scripts/get_preview_url.py",
    "scripts/migrate.mjs",
    "scripts/migrate.py",
    "scripts/sync_docs_to_public.py",
    "scripts/sync_docs_to_public_anywhere.py",
    "scripts/test_browser.mjs",
    "scripts/test_connect.py",
    "scripts/test_e2e.py",
    "scripts/test_i18n_labels.py",
}

HOST_PREFIXES = (
    "agents/lead_engineer/tasks/",
    "agents/lead_engineer/reports/",
    "agents/lead_engineer/reviews/",
    "agents/lead_engineer/meetings/",
    "agents/lead_engineer/retros/",
    "agents/lead_engineer/seminars/",
    "agents/messages/",
    "agents/runtime/",
    "agents/owner/digest/",
    "agents/beta_tester/test_cases/",
    "docs/superpowers/",
)

HOST_FILES = {
    "ralph.lock.json",
    "ralph.yml",
    "agents/lead_engineer/AUDIT-LOG.md",
    "agents/lead_engineer/STATUS.md",
    "agents/lead_engineer/assignment_log.md",
    "agents/lead_engineer/compound_log.md",
}

CORE_TEMPLATE_FILES = {
    "AGENT_RUNTIME.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CURSOR.md",
    "GEMINI.md",
    "agents/SKILL-STRUCTURE.md",
    "agents/roles.yml",
    "agents/lead_engineer/GOTCHAS.md",
    "agents/lead_engineer/RALPH-PROMPT-SCAFFOLD.md",
    "agents/lead_engineer/REPORTING-FORMAT.md",
    "agents/lead_engineer/TOKEN-BUDGET.md",
}

CORE_TEMPLATE_PREFIXES = (
    "docs/agent_bootstrap/",
    "specs/agent_loop/",
)

CORE_PACKAGE_PREFIXES = (
    "packages/ralph-automation/",
)

CORE_SCRIPT_NAMES = {
    "agent_context_packet.py",
    "agent_console.py",
    "agent_live_session.py",
    "agent_loop.py",
    "agent_loop_runner.py",
    "agent_observer.py",
    "agent_orchestrator.py",
    "agent_provider.py",
    "agent_retro.py",
    "agent_seminar.py",
    "agent_terminal.py",
    "agent_worker.py",
    "ambiguity_scan.py",
    "auto_dispatch.py",
    "auto_merge.py",
    "auto_runner.py",
    "backlog_sweep.py",
    "beta_tester_due.py",
    "budget_estimate.py",
    "build_task_index.py",
    "check_agent_docs.py",
    "check_messages.py",
    "check_repo_structure.py",
    "check_skill_structure.py",
    "claude_cli_probe.py",
    "codex_subagent_bridge.py",
    "collab_log.py",
    "compound_metrics.py",
    "cycle_gate.py",
    "doc_health_report.py",
    "doc_steward_due.py",
    "env_check.py",
    "eval_harness.py",
    "fs_watch.py",
    "generate_report_views.py",
    "generate_script_index.py",
    "generate_views.py",
    "install_hooks.py",
    "kedb_search.py",
    "local_schedule_daemon.py",
    "macro_detect.py",
    "model_routing.py",
    "now.py",
    "precommit_check.py",
    "promote_retro_forward.py",
    "prompt_clarity_hook.py",
    "qa_negotiation.py",
    "query_reports.py",
    "query_tasks.py",
    "ralph_migration_inventory.py",
    "role_mentions.py",
    "schedule.py",
    "schedule_task.py",
    "scribe_due.py",
    "secretary_digest.py",
    "session_start_hook.py",
    "subagent_bridge.py",
    "subagent_council.py",
    "subagent_dispatch.py",
    "task_api.py",
    "task_events.py",
    "task_mcp.py",
    "validate_task_schema.py",
    "verify_sdk_backend.py",
}

CORE_SCRIPT_PREFIXES = (
    "scripts/providers/",
)

FORBIDDEN_EXPORT_PREFIXES = (*LOCAL_PREFIXES, *PRODUCT_PREFIXES, *HOST_PREFIXES)


@dataclass(frozen=True)
class InventoryItem:
    path: str
    classification: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "classification": self.classification,
            "reason": self.reason,
        }


def _normalize(rel: str | Path) -> str:
    raw = str(rel).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    return PurePosixPath(raw).as_posix()


def _starts_with(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel.startswith(prefix) for prefix in prefixes)


def _is_cycle_file(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return rel.startswith("agents/lead_engineer/") and name.startswith("CYCLE-") and name.endswith(".md")


def _is_role_skill(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return len(parts) == 3 and parts[0] == "agents" and parts[2] == "SKILL.md"


def _is_core_test(rel: str) -> bool:
    if not rel.startswith("scripts/test_") or not rel.endswith(".py"):
        return False
    tested_name = rel.removeprefix("scripts/test_").removesuffix(".py") + ".py"
    return tested_name in CORE_SCRIPT_NAMES


def classify_path(rel: str | Path) -> tuple[str, str]:
    path = _normalize(rel)
    name = PurePosixPath(path).name

    if path in LOCAL_FILES or _starts_with(path, LOCAL_PREFIXES):
        return "local", "machine-specific settings or generated local runtime state"

    if path in PRODUCT_SCRIPTS or _starts_with(path, PRODUCT_PREFIXES):
        return "product", "host product app, data, deployment, or manual surface"

    if path in HOST_FILES or _is_cycle_file(path) or _starts_with(path, HOST_PREFIXES):
        return "host-state", "project-specific tasks, reports, audit history, messages, or runtime state"

    if path in CORE_TEMPLATE_FILES or _is_role_skill(path) or _starts_with(path, CORE_TEMPLATE_PREFIXES):
        return "core-template", "reusable role, bootstrap, or operating template"

    if _starts_with(path, CORE_PACKAGE_PREFIXES):
        return "core", "reusable automation package candidate"

    if path.startswith(CORE_SCRIPT_PREFIXES):
        return "core", "provider/runtime package candidate"

    if path.startswith("scripts/") and (name in CORE_SCRIPT_NAMES or _is_core_test(path)):
        return "core", "reusable automation script or its focused test"

    return "review", "ambiguous or product-adjacent; requires manual review before export"


def iter_repo_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if path.is_file():
            paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix().lower())


def analyze(root: Path) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for path in iter_repo_paths(root):
        rel = path.relative_to(root).as_posix()
        classification, reason = classify_path(rel)
        items.append(InventoryItem(rel, classification, reason))
    return items


def export_candidates(items: list[InventoryItem]) -> list[InventoryItem]:
    return [item for item in items if item.classification in {"core", "core-template"}]


def unsafe_export_items(items: list[InventoryItem]) -> list[InventoryItem]:
    unsafe: list[InventoryItem] = []
    for item in export_candidates(items):
        if item.path in LOCAL_FILES or item.path in PRODUCT_SCRIPTS or item.path in HOST_FILES:
            unsafe.append(item)
            continue
        if _starts_with(item.path, FORBIDDEN_EXPORT_PREFIXES) or _is_cycle_file(item.path):
            unsafe.append(item)
    return unsafe


def render(items: list[InventoryItem], *, limit: int | None = 20) -> str:
    counts = Counter(item.classification for item in items)
    lines = [
        "# Ralph Migration Inventory",
        "",
        "This report classifies files before extracting reusable automation into a GitHub-hosted core.",
        "",
        "| Class | Count |",
        "|-------|------:|",
    ]
    for classification in CLASSES:
        lines.append(f"| {classification} | {counts.get(classification, 0)} |")
    lines.extend(["", "## Export Candidates", "", "| Path | Class | Reason |", "|------|-------|--------|"])
    candidates = export_candidates(items)
    sample = candidates if limit is None else candidates[:limit]
    if not sample:
        lines.append("| - | - | No export candidates found. |")
    else:
        for item in sample:
            lines.append(f"| `{item.path}` | {item.classification} | {item.reason} |")
        if limit is not None and len(candidates) > limit:
            lines.append(f"| ... | ... | {len(candidates) - limit} more export candidates omitted. |")
    unsafe = unsafe_export_items(items)
    lines.extend(["", "## Safety Check", ""])
    if unsafe:
        lines.append("R: unsafe export candidates found.")
        for item in unsafe:
            lines.append(f"- `{item.path}` classified as {item.classification}: {item.reason}")
    else:
        lines.append("G: no product, host-state, or local paths are selected for core export.")
    return "\n".join(lines)


def run_inventory(
    root: Path,
    *,
    json_output: bool = False,
    check: bool = False,
    limit: int = 20,
) -> int:
    items = analyze(root)
    if json_output:
        print(json.dumps([item.as_dict() for item in items], ensure_ascii=False, indent=2))
    else:
        print(render(items, limit=None if limit < 0 else limit))

    unsafe = unsafe_export_items(items)
    if check:
        if unsafe:
            print(f"ERROR: {len(unsafe)} unsafe export candidate(s) found.", file=sys.stderr)
            return 1
        print("OK: migration inventory export boundary is safe.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify files for Ralph automation migration")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repo root")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable inventory")
    parser.add_argument("--check", action="store_true", help="Fail if unsafe paths are export candidates")
    parser.add_argument("--limit", type=int, default=20, help="Text report export-candidate sample size")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run_inventory(args.root, json_output=args.json, check=args.check, limit=args.limit)
