from pathlib import Path

import scripts.ralph_migration_inventory as rmi


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _class(rel: str) -> str:
    classification, _reason = rmi.classify_path(rel)
    return classification


def test_product_paths_do_not_classify_as_core():
    assert _class("public/app.html") == "product"
    assert _class("Managed database/schema.sql") == "product"
    assert _class("docs/manuals/spot/user_manual.md") == "product"
    assert _class("docs/vendor_docs/vendor.pdf") == "product"
    assert _class("scripts/migrate.py") == "product"
    assert _class("scripts/deploy_edge_function.py") == "product"


def test_host_state_paths_do_not_classify_as_core():
    assert _class("agents/lead_engineer/tasks/TASK-250-ralph-automation-github-sync.md") == "host-state"
    assert _class("agents/lead_engineer/AUDIT-LOG.md") == "host-state"
    assert _class("agents/lead_engineer/CYCLE-091.md") == "host-state"
    assert _class("agents/runtime/events/collab-2026-06-07.jsonl") == "host-state"
    assert _class("agents/messages/inbox/MSG-20260607-000000-test.md") == "host-state"
    assert _class("docs/superpowers/plans/2026-06-07-ralph-automation-github-sync.md") == "host-state"
    assert _class("ralph.yml") == "host-state"


def test_local_paths_do_not_classify_as_core():
    assert _class(".env") == "local"
    assert _class(".codex/config.toml") == "local"
    assert _class(".claude/settings.json") == "local"
    assert _class("schedule_runs/latest.md") == "local"
    assert _class("tasks.index.json") == "local"


def test_reusable_automation_classifies_as_core_or_template():
    assert _class("scripts/agent_worker.py") == "core"
    assert _class("scripts/auto_runner.py") == "core"
    assert _class("scripts/check_agent_docs.py") == "core"
    assert _class("scripts/providers/claude.py") == "core"
    assert _class("scripts/test_agent_worker.py") == "core"
    assert _class("scripts/ralph_migration_inventory.py") == "core"
    assert _class("scripts/test_ralph_migration_inventory.py") == "core"
    assert _class("agents/roles.yml") == "core-template"
    assert _class("agents/qa/SKILL.md") == "core-template"
    assert _class("docs/agent_bootstrap/codex.md") == "core-template"


def test_review_is_default_for_ambiguous_files():
    assert _class("README.md") == "review"
    assert _class("scripts/random_helper.py") == "review"
    assert _class("docs/design/shell.md") == "review"


def test_analyze_and_check_boundary_on_tmp_repo(tmp_path):
    _touch(tmp_path / "scripts" / "agent_worker.py")
    _touch(tmp_path / "scripts" / "providers" / "claude.py")
    _touch(tmp_path / "public" / "index.html")
    _touch(tmp_path / "agents" / "lead_engineer" / "tasks" / "TASK-001-demo.md")
    _touch(tmp_path / ".env")
    _touch(tmp_path / "agents" / "qa" / "SKILL.md")

    items = rmi.analyze(tmp_path)
    by_path = {item.path: item.classification for item in items}

    assert by_path["scripts/agent_worker.py"] == "core"
    assert by_path["scripts/providers/claude.py"] == "core"
    assert by_path["agents/qa/SKILL.md"] == "core-template"
    assert by_path["public/app.html"] == "product"
    assert by_path["agents/lead_engineer/tasks/TASK-001-demo.md"] == "host-state"
    assert by_path[".env"] == "local"
    assert rmi.unsafe_export_items(items) == []
    assert rmi.main(["--root", str(tmp_path), "--check", "--limit", "5"]) == 0


def test_render_outputs_counts_and_safety_line():
    items = [
        rmi.InventoryItem("scripts/agent_worker.py", "core", "runtime"),
        rmi.InventoryItem("public/app.html", "product", "product"),
    ]

    report = rmi.render(items)

    assert "# Ralph Migration Inventory" in report
    assert "| core | 1 |" in report
    assert "| product | 1 |" in report
    assert "G: no product, host-state, or local paths are selected for core export." in report
