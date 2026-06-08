import hashlib
import json
import subprocess
import sys

from pathlib import Path

from ralph_automation import cli as cli_module
from ralph_automation.cli import main
from ralph_automation.config import load_config
from ralph_automation.exporter import build_export_plan
from ralph_automation.host_update import _template_check_step
from ralph_automation.host_update import build_update_execution
from ralph_automation.host_update import build_update_plan
from ralph_automation.host_update import run_update
from ralph_automation.inventory import classify_path
from ralph_automation.lock import build_lock_plan
from ralph_automation.publish_bundle import build_bundle_plan
from ralph_automation.publish_check import analyze as analyze_publish
from ralph_automation.publish_github_plan import build_github_plan
from ralph_automation.publish_github_status import CommandResult
from ralph_automation.publish_github_status import build_github_status
from ralph_automation.publish_github_execute import build_github_execution
from ralph_automation.publish_github_execute import run_github_publish
from ralph_automation.release_preflight import build_preflight_plan
from ralph_automation.publish_tag_smoke import build_tag_smoke_plan
from ralph_automation.sanitize import analyze as analyze_sanitize
from ralph_automation.sync import build_sync_plan


def _write(path: Path, text: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _digest(text: str) -> str:
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _write_host_config(root: Path, *, remote_url: str = "https://github.com/example/ralph-automation.git", ref: str = "v0.1.0", package: str = "ralph-automation"):
    _write(
        root / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                f"  package: {package}",
                f"  remote_url: {remote_url}",
                f"  ref: {ref}",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )


def _write_public_source(root: Path):
    _write(root / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(root / "README.md", "# ralph\n")
    _write(root / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(root / "src" / "ralph_automation" / "__init__.py", "")
    _write(root / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(root / ".gitignore", "/templates/\n")


def test_inventory_keeps_product_and_host_state_out_of_core():
    assert classify_path("scripts/agent_worker.py")[0] == "core"
    assert classify_path("public/index.html")[0] == "product"
    assert classify_path("supabase/schema.sql")[0] == "product"
    assert classify_path("agents/lead_engineer/tasks/TASK-001-demo.md")[0] == "host-state"
    assert classify_path("ralph.yml")[0] == "host-state"
    assert classify_path("ralph.lock.json")[0] == "host-state"


def test_sync_check_reads_host_config_without_writing(tmp_path, capsys):
    config = tmp_path / "ralph.yml"
    config.write_text(
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
        encoding="utf-8",
    )

    assert main(["sync", "--root", str(tmp_path), "--check"]) == 0
    out = capsys.readouterr().out

    assert "project=demo" in out
    assert "allow_silent_overwrite=false" in out
    assert config.read_text(encoding="utf-8").startswith("project: demo")


def test_config_reads_upstream_dependency_contract(tmp_path):
    config = tmp_path / "ralph.yml"
    config.write_text(
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_config(tmp_path)

    assert loaded.upstream_package == "ralph-automation"
    assert loaded.upstream_remote_url == "https://github.com/example/ralph-automation.git"
    assert loaded.upstream_ref == "v0.1.0"


def test_update_plan_uses_host_upstream_for_install_and_sync_commands(tmp_path):
    _write(
        tmp_path / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    install_dir = tmp_path / ".ralph" / "vendor"

    plan = build_update_plan(tmp_path, install_dir)
    command_text = "\n".join(plan.commands)

    assert plan.findings == ()
    assert plan.install_spec == "git+https://github.com/example/ralph-automation.git@v0.1.0"
    assert "python -m pip install --target" in command_text
    assert str(install_dir.resolve()) in command_text
    assert "sys.path.insert(0," in command_text
    assert "from ralph_automation.cli import main" in command_text
    assert "template_sentinel" in command_text
    assert "raise SystemExit(0 if sentinel else 1)" in command_text
    assert "sync" in command_text
    assert "--check" in command_text
    assert "--diff" in command_text
    assert "--apply" in command_text
    assert "lock" in command_text
    assert "--write" in command_text


def test_update_execution_check_runs_install_and_installed_sync_check(tmp_path):
    _write(
        tmp_path / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )

    execution = build_update_execution(tmp_path, tmp_path / ".ralph" / "vendor", mode="check")

    assert [step.name for step in execution.steps] == ["install-upstream", "verify-templates", "sync-check"]
    assert "git+https://github.com/example/ralph-automation.git@v0.1.0" in execution.steps[0].args
    assert "sync" in execution.steps[2].args[-1]
    assert "--check" in execution.steps[2].args[-1]


def test_update_template_sentinel_fails_when_installed_templates_are_missing(tmp_path):
    install_dir = tmp_path / "install"
    _write(
        install_dir / "ralph_automation" / "sync.py",
        "from pathlib import Path\n"
        "def default_template_root():\n"
        "    return Path(__file__).resolve().parent / 'templates' / 'project'\n",
    )
    _write(install_dir / "ralph_automation" / "__init__.py", "")

    step = _template_check_step(install_dir)
    result = subprocess.run(step.args, check=False, capture_output=True, text=True)

    assert result.returncode == 1
    assert "template_sentinel=False" in result.stdout


def test_update_template_sentinel_passes_when_installed_templates_exist(tmp_path):
    install_dir = tmp_path / "install"
    _write(
        install_dir / "ralph_automation" / "sync.py",
        "from pathlib import Path\n"
        "def default_template_root():\n"
        "    return Path(__file__).resolve().parent / 'templates' / 'project'\n",
    )
    _write(install_dir / "ralph_automation" / "__init__.py", "")
    _write(install_dir / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")

    step = _template_check_step(install_dir)
    result = subprocess.run(step.args, check=False, capture_output=True, text=True)

    assert result.returncode == 0
    assert "template_sentinel=True" in result.stdout


def test_update_execution_apply_runs_sync_apply_and_lock_write(tmp_path):
    _write(
        tmp_path / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )

    execution = build_update_execution(tmp_path, tmp_path / ".ralph" / "vendor", mode="apply")

    assert [step.name for step in execution.steps] == [
        "install-upstream",
        "verify-templates",
        "sync-check",
        "sync-diff",
        "sync-apply",
        "sync-check",
        "lock-write",
    ]
    assert "--apply" in execution.steps[4].args[-1]
    assert "--check" in execution.steps[5].args[-1]
    assert "lock" in execution.steps[6].args[-1]
    assert "--write" in execution.steps[6].args[-1]


def test_update_execution_blocks_unsafe_install_dir(tmp_path):
    _write_host_config(tmp_path)

    root_execution = build_update_execution(tmp_path, tmp_path, mode="check")
    scripts_execution = build_update_execution(tmp_path, tmp_path / "scripts" / "vendor", mode="check")

    assert "unsafe-install-dir" in {finding.kind for finding in root_execution.findings}
    assert "unsafe-install-dir" in {finding.kind for finding in scripts_execution.findings}


def test_update_execution_blocks_non_empty_install_dir(tmp_path):
    _write_host_config(tmp_path)
    install_dir = tmp_path / ".tmp" / "ralph-upstream"
    _write(install_dir / "old.txt", "stale\n")

    execution = build_update_execution(tmp_path, install_dir, mode="check")

    assert "install-dir-not-empty" in {finding.kind for finding in execution.findings}


def test_update_execution_requires_trusted_upstream_contract(tmp_path):
    _write_host_config(tmp_path, remote_url="https://gitlab.com/example/ralph-automation.git", ref="main", package="other")

    execution = build_update_execution(tmp_path, tmp_path / ".tmp" / "ralph-upstream", mode="check")
    kinds = {finding.kind for finding in execution.findings}

    assert "unexpected-upstream-package" in kinds
    assert "non-github-upstream-remote-url" in kinds
    assert "mutable-upstream-ref" in kinds


def test_update_plan_check_uses_same_trust_findings_as_execution(tmp_path):
    _write_host_config(tmp_path, remote_url="https://gitlab.com/example/ralph-automation.git", ref="main", package="other")

    plan = build_update_plan(tmp_path, tmp_path)
    kinds = {finding.kind for finding in plan.findings}

    assert "unexpected-upstream-package" in kinds
    assert "non-github-upstream-remote-url" in kinds
    assert "mutable-upstream-ref" in kinds
    assert "unsafe-install-dir" in kinds
    assert main(["update-plan", "--root", str(tmp_path), "--install-dir", str(tmp_path), "--check"]) == 1


def test_update_plan_rejects_v_prefixed_non_semver_branch_refs(tmp_path):
    _write_host_config(tmp_path, ref="v-main")

    plan = build_update_plan(tmp_path, tmp_path / ".tmp" / "ralph-upstream")

    assert "mutable-upstream-ref" in {finding.kind for finding in plan.findings}


def test_run_update_stops_on_first_failed_step(tmp_path, capsys):
    _write(
        tmp_path / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 7 if step.name == "verify-templates" else 0

    assert run_update(tmp_path, tmp_path / ".ralph" / "vendor", mode="apply", runner=fake_runner) == 7

    out = capsys.readouterr().out
    assert calls == ["install-upstream", "verify-templates"]
    assert "failed_step=verify-templates" in out
    assert "sync-apply" not in calls


def test_lock_plan_tracks_host_upstream_version_and_template_digest(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    _write(
        host / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    _write(templates / "scripts" / "agent_worker.py", "print('worker')\n")
    _write(templates / "docs" / "ralph-automation" / "README.md", "# ralph\n")

    plan = build_lock_plan(host, template_root=templates)

    assert {finding.kind for finding in plan.findings} == {"missing-lock-file"}
    assert plan.record["project"] == "demo"
    assert plan.record["upstream"]["package"] == "ralph-automation"
    assert plan.record["upstream"]["remote_url"] == "https://github.com/example/ralph-automation.git"
    assert plan.record["upstream"]["ref"] == "v0.1.0"
    assert plan.record["installed"]["package_version"] == "0.1.0"
    assert plan.record["installed"]["template_files"] == 2
    assert plan.record["installed"]["template_digest"].startswith("sha256:")
    assert plan.record["installed"]["managed_files"]["scripts/agent_worker.py"].startswith("sha256:")


def test_lock_plan_ignores_installed_template_pycache(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    _write(
        host / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    _write(templates / "scripts" / "agent_worker.py", "print('worker')\n")
    (templates / "scripts" / "__pycache__").mkdir(parents=True)
    (templates / "scripts" / "__pycache__" / "agent_worker.cpython-310.pyc").write_bytes(b"compiled")

    plan = build_lock_plan(host, template_root=templates)

    assert plan.record["installed"]["template_files"] == 1


def test_lock_digest_is_stable_across_template_line_endings(tmp_path):
    host = tmp_path / "host"
    lf_templates = tmp_path / "lf"
    crlf_templates = tmp_path / "crlf"
    _write(
        host / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    (lf_templates / "scripts").mkdir(parents=True)
    (crlf_templates / "scripts").mkdir(parents=True)
    (lf_templates / "scripts" / "agent_worker.py").write_bytes(b"line1\nline2\n")
    (crlf_templates / "scripts" / "agent_worker.py").write_bytes(b"line1\r\nline2\r\n")

    lf_plan = build_lock_plan(host, template_root=lf_templates)
    crlf_plan = build_lock_plan(host, template_root=crlf_templates)

    assert lf_plan.record["installed"]["template_digest"] == crlf_plan.record["installed"]["template_digest"]


def test_lock_write_then_check_is_current(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    _write(
        host / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    _write(templates / "scripts" / "agent_worker.py", "print('worker')\n")

    assert main(["lock", "--root", str(host), "--template-root", str(templates), "--write"]) == 0
    assert main(["lock", "--root", str(host), "--template-root", str(templates), "--check"]) == 0


def test_sync_ignores_installed_template_pycache(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    _write(
        host / "ralph.yml",
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
    )
    _write(templates / "scripts" / "agent_worker.py", "print('worker')\n")
    (templates / "scripts" / "__pycache__").mkdir(parents=True)
    (templates / "scripts" / "__pycache__" / "agent_worker.cpython-310.pyc").write_bytes(b"compiled")

    plan = build_sync_plan(host, template_root=templates)

    assert [update.path for update in plan.updates] == ["scripts/agent_worker.py"]


def test_sync_updates_managed_file_when_host_matches_lock(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    old = "print('old')\n"
    new = "print('new')\n"
    _write(
        host / "ralph.yml",
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
    )
    _write(host / "scripts" / "agent_worker.py", old)
    _write(templates / "scripts" / "agent_worker.py", new)
    _write(
        host / "ralph.lock.json",
        json.dumps(
            {
                "schema": "ralph-lock/v1",
                "project": "demo",
                "upstream": {"package": "ralph-automation", "remote_url": "", "ref": ""},
                "installed": {
                    "package_version": "0.1.0",
                    "template_digest": "sha256:old",
                    "template_files": 1,
                    "managed_files": {"scripts/agent_worker.py": _digest(old)},
                },
            }
        )
        + "\n",
    )

    plan = build_sync_plan(host, template_root=templates)

    assert [(update.action, update.path) for update in plan.updates] == [("update", "scripts/agent_worker.py")]
    assert plan.conflicts == ()
    assert main(["sync", "--root", str(host), "--template-root", str(templates), "--apply"]) == 0
    assert (host / "scripts" / "agent_worker.py").read_text(encoding="utf-8") == new


def test_sync_conflicts_when_host_modified_from_locked_managed_file(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    old = "print('old')\n"
    modified = "print('host edit')\n"
    new = "print('new')\n"
    _write(
        host / "ralph.yml",
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
    )
    _write(host / "scripts" / "agent_worker.py", modified)
    _write(templates / "scripts" / "agent_worker.py", new)
    _write(
        host / "ralph.lock.json",
        json.dumps(
            {
                "schema": "ralph-lock/v1",
                "project": "demo",
                "upstream": {"package": "ralph-automation", "remote_url": "", "ref": ""},
                "installed": {
                    "package_version": "0.1.0",
                    "template_digest": "sha256:old",
                    "template_files": 1,
                    "managed_files": {"scripts/agent_worker.py": _digest(old)},
                },
            }
        )
        + "\n",
    )

    plan = build_sync_plan(host, template_root=templates)

    assert plan.updates == ()
    assert [(conflict.action, conflict.path) for conflict in plan.conflicts] == [("conflict", "scripts/agent_worker.py")]


def test_sync_check_fails_when_conflicts_exist(tmp_path):
    host = tmp_path / "host"
    templates = tmp_path / "templates"
    _write(
        host / "ralph.yml",
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
    )
    _write(host / "scripts" / "agent_worker.py", "print('host edit')\n")
    _write(templates / "scripts" / "agent_worker.py", "print('upstream')\n")

    assert main(["sync", "--root", str(host), "--template-root", str(templates), "--check"]) == 1


def test_update_plan_requires_upstream_contract(tmp_path):
    _write(
        tmp_path / "ralph.yml",
        "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n",
    )

    plan = build_update_plan(tmp_path, tmp_path / ".ralph" / "vendor")
    kinds = {finding.kind for finding in plan.findings}

    assert "missing-upstream-remote-url" in kinds
    assert "missing-upstream-ref" in kinds


def test_update_plan_blocks_placeholder_upstream_remote(tmp_path):
    _write_host_config(tmp_path, remote_url="https://github.com/OWNER/ralph-automation.git")

    plan = build_update_plan(tmp_path, tmp_path / ".tmp" / "ralph-upstream")

    assert "placeholder-remote-url" in {finding.kind for finding in plan.findings}


def test_release_preflight_aggregates_public_and_host_readiness(tmp_path):
    source = tmp_path / "source"
    host = tmp_path / "host"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")
    _write(
        host / "ralph.yml",
        "\n".join(
            [
                "project: demo",
                "upstream:",
                "  package: ralph-automation",
                "  remote_url: https://github.com/example/ralph-automation.git",
                "  ref: v0.1.0",
                "sync:",
                "  mode: check-diff-apply",
                "  allow_silent_overwrite: false",
            ]
        )
        + "\n",
    )
    assert main(
        [
            "lock",
            "--root",
            str(host),
            "--template-root",
            str(source / "src" / "ralph_automation" / "templates" / "project"),
            "--write",
        ]
    ) == 0

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=tmp_path / "bundle",
        tag_repo_dir=tmp_path / "tag-repo",
        tag_install_dir=tmp_path / "tag-install",
        github_install_dir=source / ".tmp" / "github-install",
        host_install_dir=host / ".tmp" / "host-install",
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )

    checks = {check.name: check for check in plan.checks}

    assert plan.findings_count == 0
    assert checks["host-upstream-match"].status == "ok"
    assert checks["sanitize"].status == "ok"
    assert checks["publish-check"].status == "ok"
    assert checks["publish-bundle"].detail == "files=6"
    assert checks["local-tag-smoke-plan"].status == "ok"
    assert checks["github-publish-plan"].status == "ok"
    assert checks["host-update-plan"].status == "ok"
    assert checks["host-update-command"].status == "ok"
    assert checks["host-update-command"].detail == "steps=3"
    assert checks["host-lock"].status == "ok"


def test_release_preflight_resolves_relative_work_dirs_under_source_root(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    source = checkout / ".tmp" / "public-source"
    host = source / "tests" / "fixtures" / "host"
    _write_public_source(source)
    _write_host_config(host, remote_url="https://github.com/example/ralph-automation.git")
    assert main(
        [
            "lock",
            "--root",
            str(host),
            "--template-root",
            str(source / "src" / "ralph_automation" / "templates" / "project"),
            "--write",
        ]
    ) == 0
    monkeypatch.chdir(checkout)

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=Path(".tmp/public-source"),
        tag_repo_dir=Path(".tmp/tag-repo"),
        tag_install_dir=Path(".tmp/tag-install"),
        github_install_dir=Path(".tmp/github-install"),
        host_install_dir=Path(".tmp/ralph-upstream"),
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )
    checks = {check.name: check for check in plan.checks}

    assert plan.findings_count == 0
    assert checks["publish-bundle"].status == "ok"
    assert checks["host-lock"].status == "ok"
    expected_tag_repo_uri = (source / ".tmp" / "tag-repo").resolve().as_uri()
    assert checks["local-tag-smoke-plan"].detail == f"install_spec=git+{expected_tag_repo_uri}@v0.1.0"


def test_release_preflight_blocks_host_sync_conflicts(tmp_path):
    source = tmp_path / "source"
    host = tmp_path / "host"
    _write_public_source(source)
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "print('upstream')\n")
    _write_host_config(host, remote_url="https://github.com/example/ralph-automation.git")
    _write(host / "scripts" / "agent_worker.py", "print('upstream')\n")
    assert main(
        [
            "lock",
            "--root",
            str(host),
            "--template-root",
            str(source / "src" / "ralph_automation" / "templates" / "project"),
            "--write",
        ]
    ) == 0
    _write(host / "scripts" / "agent_worker.py", "print('host edit')\n")

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=tmp_path / "bundle",
        tag_repo_dir=tmp_path / "tag-repo",
        tag_install_dir=tmp_path / "tag-install",
        github_install_dir=source / ".tmp" / "github-install",
        host_install_dir=host / ".tmp" / "host-install",
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )
    checks = {check.name: check for check in plan.checks}

    assert checks["host-lock"].status == "ok"
    assert checks["host-sync-check"].status == "blocked"
    assert "host-sync-conflict" in {finding.kind for finding in checks["host-sync-check"].findings}


def test_release_preflight_blocks_host_upstream_mismatch(tmp_path):
    source = tmp_path / "source"
    host = tmp_path / "host"
    _write_public_source(source)
    _write_host_config(host, remote_url="https://github.com/example/other.git", ref="v0.2.0")

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=tmp_path / "bundle",
        tag_repo_dir=tmp_path / "tag-repo",
        tag_install_dir=tmp_path / "tag-install",
        github_install_dir=source / ".tmp" / "github-install",
        host_install_dir=host / ".tmp" / "host-install",
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )
    checks = {check.name: check for check in plan.checks}
    kinds = {finding.kind for finding in checks["host-upstream-match"].findings}

    assert checks["host-upstream-match"].status == "blocked"
    assert "upstream-remote-url-mismatch" in kinds
    assert "upstream-ref-mismatch" in kinds


def test_release_preflight_reports_executable_host_update_findings(tmp_path):
    source = tmp_path / "source"
    host = tmp_path / "host"
    _write_public_source(source)
    _write_host_config(host)
    _write(host / ".tmp" / "host-install" / "old.txt", "stale\n")

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=tmp_path / "bundle",
        tag_repo_dir=tmp_path / "tag-repo",
        tag_install_dir=tmp_path / "tag-install",
        github_install_dir=source / ".tmp" / "github-install",
        host_install_dir=host / ".tmp" / "host-install",
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )
    checks = {check.name: check for check in plan.checks}

    assert checks["host-update-plan"].status == "blocked"
    assert "install-dir-not-empty" in {finding.kind for finding in checks["host-update-plan"].findings}
    assert checks["host-update-command"].status == "skipped"


def test_release_preflight_reports_missing_host_upstream(tmp_path):
    source = tmp_path / "source"
    host = tmp_path / "host"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")
    _write(host / "ralph.yml", "project: demo\nsync:\n  mode: check-diff-apply\n  allow_silent_overwrite: false\n")

    plan = build_preflight_plan(
        source_root=source,
        host_root=host,
        bundle_dir=tmp_path / "bundle",
        tag_repo_dir=tmp_path / "tag-repo",
        tag_install_dir=tmp_path / "tag-install",
        github_install_dir=source / ".tmp" / "github-install",
        host_install_dir=host / ".tmp" / "host-install",
        remote_url="https://github.com/example/ralph-automation.git",
        tag="v0.1.0",
    )

    checks = {check.name: check for check in plan.checks}

    assert plan.findings_count == 2
    assert checks["host-upstream-match"].status == "skipped"
    assert checks["host-update-plan"].status == "blocked"
    assert checks["host-update-command"].status == "skipped"
    assert checks["host-lock"].status == "skipped"


def test_sanitize_blocks_forbidden_public_content(tmp_path):
    local_path = "C:" + "\\Us" + "ers\\someone\\private"
    _write(tmp_path / ".env")
    _write(tmp_path / "public" / "index.html")
    _write(tmp_path / "README.md", local_path + "\n")

    findings = analyze_sanitize(tmp_path)
    kinds = {(finding.path, finding.kind) for finding in findings}

    assert (".env", "forbidden-path") in kinds
    assert ("public/index.html", "forbidden-path") in kinds
    assert ("README.md", "absolute-local-path") in kinds


def test_sanitize_blocks_forward_slash_windows_absolute_paths(tmp_path):
    local_path = "C:" + "/Us" + "ers/someone/private"
    _write(tmp_path / "README.md", f"Local path: {local_path}\n")

    findings = analyze_sanitize(tmp_path)
    kinds = {(finding.path, finding.kind) for finding in findings}

    assert ("README.md", "absolute-local-path") in kinds


def test_sanitize_ignores_generated_local_work_dirs(tmp_path):
    slash_path = "C:" + "/Us" + "ers/someone/private"
    backslash_path = "C:" + "\\Us" + "ers\\someone\\private"
    _write(tmp_path / "README.md", "# public package\n")
    _write(tmp_path / ".tmp" / "pip-install" / "direct_url.json", f'{{"url":"file:///{slash_path}"}}\n')
    _write(tmp_path / "build" / "lib" / "README.md", backslash_path + "\n")
    _write(tmp_path / "dist" / "metadata.txt", backslash_path + "\n")
    _write(tmp_path / ".pytest_cache" / "README.md", backslash_path + "\n")

    findings = analyze_sanitize(tmp_path)

    assert findings == []


def test_sanitize_blocks_forbidden_paths_nested_under_project_templates(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    nested_task = source / "src" / "ralph_automation" / "templates" / "project" / "agents" / "lead_engineer" / "tasks" / "TASK-private.md"
    _write(nested_task, "# private task\n")

    sanitize_findings = {(finding.path, finding.kind) for finding in analyze_sanitize(source)}
    github_plan = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert (nested_task.relative_to(source).as_posix(), "forbidden-template-path") in sanitize_findings
    assert "sanitize:forbidden-template-path" in {finding.kind for finding in github_plan.findings}


def test_sanitize_blocks_host_history_references_in_project_template_docs(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    template_doc = source / "src" / "ralph_automation" / "templates" / "project" / "AGENTS.md"
    _write(template_doc, "Reusable rules.\nHistorical fix: TASK-027 for Supabase RLS.\n")

    sanitize_findings = {(finding.path, finding.kind) for finding in analyze_sanitize(source)}
    github_plan = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert (template_doc.relative_to(source).as_posix(), "host-history-reference") in sanitize_findings
    assert "sanitize:host-history-reference" in {finding.kind for finding in github_plan.findings}


def test_sanitize_blocks_host_history_references_in_nested_project_templates(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    template_doc = source / "src" / "ralph_automation" / "templates" / "project" / "agents" / "backend_engineer" / "SKILL.md"
    _write(template_doc, "Reusable role.\nDo not carry TASK-027 Supabase RLS history.\n")

    sanitize_findings = {(finding.path, finding.kind) for finding in analyze_sanitize(source)}

    assert (template_doc.relative_to(source).as_posix(), "host-history-reference") in sanitize_findings


def test_export_plan_selects_only_public_core_candidates(tmp_path):
    package_root = tmp_path / "packages" / "ralph-automation"
    _write(package_root / "templates" / "project" / ".gitkeep")
    _write(tmp_path / "scripts" / "agent_worker.py", "print('worker')\n")
    _write(tmp_path / "AGENTS.md", "# reusable operating rules\n")
    _write(tmp_path / "public" / "index.html", "<main>product app</main>\n")
    _write(tmp_path / "agents" / "lead_engineer" / "tasks" / "TASK-001.md", "private task\n")

    plan = build_export_plan(tmp_path, package_root)
    creates = {item.source for item in plan.creates}

    assert "scripts/agent_worker.py" in creates
    assert "AGENTS.md" in creates
    assert "public/index.html" not in creates
    assert "agents/lead_engineer/tasks/TASK-001.md" not in creates


def test_export_apply_copies_missing_templates_and_blocks_unsafe_content(tmp_path):
    package_root = tmp_path / "packages" / "ralph-automation"
    _write(package_root / "templates" / "project" / ".gitkeep")
    _write(tmp_path / "scripts" / "agent_worker.py", "print('worker')\n")
    _write(tmp_path / "scripts" / "auto_runner.py", "OPENAI_API_" + "KEY=unsafe\n")

    assert main(["export", "--host-root", str(tmp_path), "--package-root", str(package_root), "--apply"]) == 1

    template_root = package_root / "src" / "ralph_automation" / "templates" / "project"
    assert (template_root / "scripts" / "agent_worker.py").exists() is False
    (tmp_path / "scripts" / "auto_runner.py").write_text("print('safe')\n", encoding="utf-8")

    assert main(["export", "--host-root", str(tmp_path), "--package-root", str(package_root), "--apply"]) == 0
    assert (template_root / "scripts" / "agent_worker.py").read_text(encoding="utf-8") == "print('worker')\n"
    assert (template_root / "scripts" / "auto_runner.py").read_text(encoding="utf-8") == "print('safe')\n"


def test_publish_check_requires_public_github_source_contract(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        "[project]\nname='ralph-automation'\n[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n",
    )
    _write(tmp_path / "README.md", "# ralph-automation\n")
    _write(tmp_path / "src" / "ralph_automation" / "__init__.py", "")
    _write(tmp_path / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(
        tmp_path / ".github" / "workflows" / "test.yml",
        "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n",
    )
    _write(tmp_path / ".gitignore", "/templates/\n/build/\n/src/*.egg-info/\n")

    findings = analyze_publish(tmp_path)

    assert findings == []


def test_github_workflow_runs_publish_gates_against_clean_bundle():
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "publish-bundle --source . --dest .tmp/public-source --apply" in workflow
    assert "publish-github-plan --source .tmp/public-source" in workflow
    assert "release-preflight --source .tmp/public-source" in workflow
    assert "--host-root .tmp/public-source/tests/fixtures/host" in workflow
    assert "publish-github-plan --source . --remote-url" not in workflow
    assert "release-preflight --source . --host-root" not in workflow


def test_publish_check_blocks_duplicate_top_level_templates_without_ignore(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(tmp_path / "README.md", "# ralph-automation\n")
    _write(tmp_path / "src" / "ralph_automation" / "__init__.py", "")
    _write(tmp_path / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(tmp_path / ".github" / "workflows" / "test.yml", "python -m ralph_automation.cli sanitize --root . --check\n")
    _write(tmp_path / "templates" / "project" / "scripts" / "agent_worker.py", "")

    findings = analyze_publish(tmp_path)
    details = {(finding.kind, finding.path) for finding in findings}

    assert ("duplicate-template-tree", "templates") in details


def test_publish_bundle_copies_clean_public_source_only(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(
        source / "pyproject.toml",
        "[project]\nname='ralph-automation'\n[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n",
    )
    _write(source / "README.md", "# ralph-automation\n")
    _write(source / ".gitignore", "/templates/\n/build/\n/src/*.egg-info/\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / "tests" / "test_smoke.py", "def test_smoke():\n    assert True\n")
    _write(source / "build" / "lib" / "generated.py", "stale\n")
    _write(source / "templates" / "project" / "legacy.md", "duplicate\n")

    plan = build_bundle_plan(source, dest)
    rels = {item.path for item in plan.files}

    assert "src/ralph_automation/templates/project/scripts/agent_worker.py" in rels
    assert "tests/test_smoke.py" in rels
    assert "build/lib/generated.py" not in rels
    assert "templates/project/legacy.md" not in rels

    assert main(["publish-bundle", "--source", str(source), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py").exists()
    assert (dest / "templates").exists() is False
    assert (dest / "build").exists() is False
    assert analyze_publish(dest) == []


def test_publish_bundle_refuses_non_empty_destination(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")
    _write(dest / "keep.txt", "do not overwrite\n")

    assert main(["publish-bundle", "--source", str(source), "--dest", str(dest), "--apply"]) == 1
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_publish_tag_smoke_plan_uses_file_git_tag(tmp_path):
    source = tmp_path / "source"
    repo_dir = tmp_path / "repo"
    install_dir = tmp_path / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")

    plan = build_tag_smoke_plan(source, repo_dir, install_dir, "v0.1.0")

    assert plan.findings == ()
    assert plan.install_spec.startswith("git+file:")
    assert plan.install_spec.endswith("@v0.1.0")


def test_publish_tag_smoke_refuses_non_empty_work_dirs(tmp_path):
    source = tmp_path / "source"
    repo_dir = tmp_path / "repo"
    install_dir = tmp_path / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")
    _write(repo_dir / "keep.txt", "do not overwrite\n")
    _write(install_dir / "keep.txt", "do not overwrite\n")

    plan = build_tag_smoke_plan(source, repo_dir, install_dir, "v0.1.0")
    kinds = {finding.kind for finding in plan.findings}

    assert "repo-dir-not-empty" in kinds
    assert "install-dir-not-empty" in kinds


def test_publish_github_plan_builds_owner_approved_remote_commands(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")

    plan = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        install_dir,
        tag="v0.1.0",
        branch="main",
    )

    command_text = "\n".join(plan.commands)
    work_dir = source.resolve() / ".tmp" / "github-worktree"

    assert plan.findings == ()
    assert plan.repository == "example/ralph-automation"
    assert plan.work_dir == work_dir
    assert "gh repo create example/ralph-automation --public" not in plan.commands
    repo_command_index = next(index for index, command in enumerate(plan.commands) if "gh','repo','create'" in command)
    repo_command = plan.commands[repo_command_index]
    assert "gh','repo','view'" in repo_command
    assert "gh','repo','create'" in repo_command
    assert "github-repo-not-public" in repo_command
    assert "not found" in repo_command
    assert "could not resolve to a repository" in repo_command
    assert plan.commands.index(f'cd "{work_dir}" && git tag "v0.1.0"') < repo_command_index
    assert repo_command_index < plan.commands.index(f'cd "{work_dir}" && git push -u origin "main"')
    assert f'publish-bundle --source "{source.resolve()}" --dest "{work_dir}" --apply' in command_text
    assert plan.install_spec == "git+https://github.com/example/ralph-automation.git@v0.1.0"
    assert f'cd "{work_dir}" && git -c user.name="Ralph Release" -c user.email=ralph-release@example.invalid commit -m "release v0.1.0"' in command_text
    assert f'cd "{work_dir}" && git remote add origin "https://github.com/example/ralph-automation.git"' in command_text
    assert f'cd "{work_dir}" && git push -u origin "main"' in command_text
    assert f'cd "{work_dir}" && git push origin "v0.1.0"' in command_text
    assert "python -m pip install --target" in command_text
    assert str(install_dir.resolve()) in command_text
    assert "sys.path.insert(0," in command_text
    assert "from ralph_automation.sync import default_template_root" in command_text
    assert "template_sentinel" in command_text
    assert "raise SystemExit(0 if sentinel else 1)" in command_text
    assert "'publish-github-status'" in command_text
    assert "'--remote-url','https://github.com/example/ralph-automation.git'" in command_text
    assert "'--branch','main'" in command_text
    assert "'--require-workflow','--wait-workflow','--workflow-head-sha',sha,'--check'" in command_text
    assert "subprocess.check_output" in command_text
    assert "rev-parse" in command_text
    assert "$(" not in command_text


def test_publish_github_plan_quotes_paths_with_spaces(tmp_path):
    source = tmp_path / "source with spaces"
    install_dir = source / ".tmp" / "install dir"
    _write_public_source(source)

    plan = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        install_dir,
    )

    command_text = "\n".join(plan.commands)
    work_dir = source.resolve() / ".tmp" / "github-worktree"

    assert f'--source "{source.resolve()}"' in command_text
    assert f'--dest "{work_dir}"' in command_text
    assert f'cd "{work_dir}" && git init' in command_text
    assert f'--target "{install_dir.resolve()}"' in command_text


def test_publish_github_plan_blocks_unsafe_branch_and_tag_refs(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    plan = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        tag="v0.1.0;rm",
        branch="main;rm",
    )

    kinds = {finding.kind for finding in plan.findings}

    assert "unsafe-git-branch" in kinds
    assert "unsafe-git-tag" in kinds


def test_publish_github_plan_parses_ssh_remote_repository(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")

    plan = build_github_plan(source, "git@github.com:example/ralph-automation.git", install_dir)

    assert plan.repository == "example/ralph-automation"
    assert plan.install_spec == "git+ssh://git@github.com/example/ralph-automation.git@v0.1.0"


def test_publish_github_plan_reports_malformed_github_remote(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")

    plan = build_github_plan(source, "https://github.com/example", install_dir)
    kinds = {finding.kind for finding in plan.findings}

    assert "malformed-github-remote-url" in kinds


def test_publish_github_plan_blocks_placeholder_remote_owner(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    plan = build_github_plan(
        source,
        "https://github.com/OWNER/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert "placeholder-remote-url" in {finding.kind for finding in plan.findings}


def test_publish_github_plan_requires_github_remote(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write(source / "pyproject.toml", "[tool.setuptools.package-data]\nralph_automation=['templates/project/**/*']\n")
    _write(source / "README.md", "# ralph\n")
    _write(source / ".github" / "workflows" / "test.yml", "python -m pytest tests -q\npython -m ralph_automation.cli sanitize --root . --check\n")
    _write(source / "src" / "ralph_automation" / "__init__.py", "")
    _write(source / "src" / "ralph_automation" / "templates" / "project" / "scripts" / "agent_worker.py", "")
    _write(source / ".gitignore", "/templates/\n")

    plan = build_github_plan(source, "https://gitlab.com/example/ralph-automation.git", install_dir)
    kinds = {finding.kind for finding in plan.findings}

    assert "non-github-remote-url" in kinds


def test_publish_github_plan_blocks_files_outside_clean_bundle_contract(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write_public_source(source)
    _write(source / "docs" / "private-note.md", "not part of the public bundle\n")

    plan = build_github_plan(source, "https://github.com/example/ralph-automation.git", install_dir)
    findings = {(finding.path, finding.kind) for finding in plan.findings}

    assert ("docs/private-note.md", "unexpected-source-file") in findings


def test_publish_github_plan_blocks_nested_build_files_git_would_add(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write_public_source(source)
    _write(source / "src" / "ralph_automation" / "build" / "private.txt", "host-only\n")

    plan = build_github_plan(source, "https://github.com/example/ralph-automation.git", install_dir)
    findings = {(finding.path, finding.kind) for finding in plan.findings}

    assert ("src/ralph_automation/build/private.txt", "unexpected-source-file") in findings


def test_publish_github_plan_blocks_existing_git_repository_source(tmp_path):
    source = tmp_path / "source"
    install_dir = source / ".tmp" / "install"
    _write_public_source(source)
    _write(source / ".git" / "config", "[core]\n")

    plan = build_github_plan(source, "https://github.com/example/ralph-automation.git", install_dir)
    kinds = {finding.kind for finding in plan.findings}

    assert "source-git-repo-exists" in kinds


def test_publish_github_plan_requires_safe_empty_install_dir(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    _write(source / ".tmp" / "install" / "old.txt", "stale\n")

    root_install = build_github_plan(source, "https://github.com/example/ralph-automation.git", source)
    outside_install = build_github_plan(source, "https://github.com/example/ralph-automation.git", tmp_path / "outside")
    non_empty_install = build_github_plan(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert "unsafe-github-install-dir" in {finding.kind for finding in root_install.findings}
    assert "unsafe-github-install-dir" in {finding.kind for finding in outside_install.findings}
    assert "github-install-dir-not-empty" in {finding.kind for finding in non_empty_install.findings}


def test_publish_github_status_reports_invalid_auth_without_repo_probe():
    calls = []

    def fake_runner(args):
        calls.append(args)
        return CommandResult(args=args, returncode=1, stdout="", stderr="token is invalid\n")

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)

    kinds = {finding.kind for finding in status.findings}
    assert status.repository == "example/ralph-automation"
    assert "gh-auth-unavailable" in kinds
    assert calls == [("gh", "auth", "status")]


def test_publish_github_status_prefers_diagnostic_auth_line():
    def fake_runner(args):
        return CommandResult(
            args=args,
            returncode=1,
            stdout="github.com\n  X Failed to log in to github.com account user\n  - The token in default is invalid.\n",
            stderr="",
        )

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)
    checks = {check.name: check for check in status.checks}

    assert "Failed to log in" in checks["auth"].detail


def test_publish_github_status_checks_user_and_repo_when_auth_ok():
    calls = []

    def fake_runner(args):
        calls.append(args)
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\nToken scopes: 'repo', 'workflow'\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC","url":"https://github.com/example/ralph-automation"}\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)
    checks = {check.name: check for check in status.checks}

    assert status.findings == ()
    assert checks["auth"].status == "ok"
    assert checks["user"].detail == "login=example"
    assert checks["repo"].detail == "available"
    assert calls[-1] == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url")


def test_publish_github_status_blocks_missing_workflow_scope_when_auth_ok():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\nToken scopes: 'repo'\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        raise AssertionError(args)

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)
    kinds = {finding.kind for finding in status.findings}
    checks = {check.name: check for check in status.checks}

    assert "gh-workflow-scope-missing" in kinds
    assert checks["scope"].status == "blocked"


def test_publish_github_status_blocks_private_repo_when_auth_ok():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PRIVATE","url":"https://github.com/example/ralph-automation"}\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)
    kinds = {finding.kind for finding in status.findings}
    checks = {check.name: check for check in status.checks}

    assert "github-repo-not-public" in kinds
    assert checks["repo"].status == "blocked"


def test_publish_github_status_blocks_repo_with_missing_visibility():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation"}\n', stderr="")
        raise AssertionError(args)

    status = build_github_status("https://github.com/example/ralph-automation.git", runner=fake_runner)
    kinds = {finding.kind for finding in status.findings}
    checks = {check.name: check for check in status.checks}

    assert "github-repo-visibility-missing" in kinds
    assert checks["repo"].status == "blocked"


def test_publish_github_status_requires_workflow_success_when_requested():
    calls = []

    def fake_runner(args):
        calls.append(args)
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args == (
            "gh",
            "run",
            "list",
            "--repo",
            "example/ralph-automation",
            "--branch",
            "main",
            "--workflow",
            "test",
            "--limit",
            "1",
            "--json",
            "status,conclusion,headSha,url,workflowName",
        ):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"success","headSha":"abc123","url":"https://github.com/example/ralph-automation/actions/runs/1","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        runner=fake_runner,
    )
    checks = {check.name: check for check in status.checks}

    assert status.findings == ()
    assert checks["workflow"].status == "ok"
    assert "conclusion=success" in checks["workflow"].detail
    assert calls[-1][0:3] == ("gh", "run", "list")


def test_publish_github_status_blocks_successful_run_from_wrong_workflow():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"success","headSha":"newsha","url":"https://github.com/example/ralph-automation/actions/runs/8","workflowName":"docs"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        workflow_head_sha="newsha",
        runner=fake_runner,
    )
    kinds = {finding.kind for finding in status.findings}

    assert "github-workflow-wrong-name" in kinds


def test_publish_github_status_flags_failed_workflow_when_required():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"failure","headSha":"abc123","url":"https://github.com/example/ralph-automation/actions/runs/2","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        runner=fake_runner,
    )
    kinds = {finding.kind for finding in status.findings}
    checks = {check.name: check for check in status.checks}

    assert "github-workflow-not-success" in kinds
    assert checks["workflow"].status == "blocked"


def test_publish_github_status_waits_until_workflow_success_when_requested():
    workflow_calls = 0

    def fake_runner(args):
        nonlocal workflow_calls
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            workflow_calls += 1
            if workflow_calls == 1:
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout='[{"status":"in_progress","conclusion":"","headSha":"abc123","url":"https://github.com/example/ralph-automation/actions/runs/3","workflowName":"test"}]\n',
                    stderr="",
                )
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"success","headSha":"abc123","url":"https://github.com/example/ralph-automation/actions/runs/3","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        wait_workflow=True,
        workflow_timeout_seconds=5,
        workflow_poll_seconds=0,
        runner=fake_runner,
    )
    checks = {check.name: check for check in status.checks}

    assert status.findings == ()
    assert workflow_calls == 2
    assert checks["workflow"].status == "ok"


def test_publish_github_status_blocks_successful_workflow_for_different_head_sha():
    def fake_runner(args):
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"success","headSha":"oldsha","url":"https://github.com/example/ralph-automation/actions/runs/5","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        workflow_head_sha="newsha",
        runner=fake_runner,
    )
    kinds = {finding.kind for finding in status.findings}

    assert "github-workflow-head-sha-mismatch" in kinds


def test_publish_github_status_waits_for_matching_head_sha_success():
    workflow_calls = 0

    def fake_runner(args):
        nonlocal workflow_calls
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            workflow_calls += 1
            if workflow_calls == 1:
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout='[{"status":"completed","conclusion":"success","headSha":"oldsha","url":"https://github.com/example/ralph-automation/actions/runs/5","workflowName":"test"}]\n',
                    stderr="",
                )
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"completed","conclusion":"success","headSha":"newsha","url":"https://github.com/example/ralph-automation/actions/runs/6","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        wait_workflow=True,
        workflow_head_sha="newsha",
        workflow_timeout_seconds=5,
        workflow_poll_seconds=0,
        runner=fake_runner,
    )

    assert status.findings == ()
    assert workflow_calls == 2


def test_publish_github_status_times_out_waiting_for_workflow_success():
    workflow_calls = 0

    def fake_runner(args):
        nonlocal workflow_calls
        if args == ("gh", "auth", "status"):
            return CommandResult(args=args, returncode=0, stdout="Logged in\n", stderr="")
        if args == ("gh", "api", "user", "--jq", ".login"):
            return CommandResult(args=args, returncode=0, stdout="example\n", stderr="")
        if args == ("gh", "repo", "view", "example/ralph-automation", "--json", "nameWithOwner,visibility,url"):
            return CommandResult(args=args, returncode=0, stdout='{"nameWithOwner":"example/ralph-automation","visibility":"PUBLIC"}\n', stderr="")
        if args[0:3] == ("gh", "run", "list"):
            workflow_calls += 1
            return CommandResult(
                args=args,
                returncode=0,
                stdout='[{"status":"queued","conclusion":"","headSha":"abc123","url":"https://github.com/example/ralph-automation/actions/runs/4","workflowName":"test"}]\n',
                stderr="",
            )
        raise AssertionError(args)

    status = build_github_status(
        "https://github.com/example/ralph-automation.git",
        branch="main",
        require_workflow=True,
        wait_workflow=True,
        workflow_timeout_seconds=0,
        workflow_poll_seconds=0,
        runner=fake_runner,
    )
    kinds = {finding.kind for finding in status.findings}

    assert workflow_calls == 1
    assert "github-workflow-timeout" in kinds


def test_publish_github_execution_stops_before_mutation_when_auth_fails(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 1 if step.name == "gh-auth-status" else 0

    exit_code = run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
    )

    assert exit_code == 1
    assert calls == ["gh-auth-status"]


def test_publish_github_execution_stops_before_mutation_when_workflow_scope_missing(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 1 if step.name == "gh-workflow-scope" else 0

    exit_code = run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
    )

    assert exit_code == 1
    assert calls == ["gh-auth-status", "gh-workflow-scope"]


def test_publish_github_execution_skips_create_when_repo_exists(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 0

    assert run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
    ) == 0

    assert "repo-ensure-public" in calls
    assert "repo-view" not in calls
    assert "repo-create" not in calls
    assert calls[-1] == "github-status"


def test_publish_github_execution_creates_repo_when_missing(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 0

    assert run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
    ) == 0

    assert calls.index("git-tag") < calls.index("repo-ensure-public")
    assert "push-tag" in calls
    assert calls[-1] == "github-status"


def test_publish_github_execution_uses_fail_closed_public_repo_ensure_step(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )
    step_names = [step.name for step in execution.steps]
    ensure_step = execution.steps[step_names.index("repo-ensure-public")]
    ensure_command = " ".join(ensure_step.args)

    assert "repo-view" not in step_names
    assert "repo-create" not in step_names
    assert step_names.index("git-tag") < step_names.index("repo-ensure-public")
    assert step_names.index("repo-ensure-public") < step_names.index("push-branch")
    assert "visibility" in ensure_command
    assert "github-repo-not-public" in ensure_command
    assert "could not resolve to a repository" in ensure_command


def test_publish_github_execution_finishes_local_release_before_repo_create(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )
    step_names = [step.name for step in execution.steps]

    assert step_names.index("prepare-worktree") < step_names.index("repo-ensure-public")
    assert step_names.index("git-commit") < step_names.index("repo-ensure-public")
    assert step_names.index("git-tag") < step_names.index("repo-ensure-public")
    assert step_names.index("repo-ensure-public") < step_names.index("push-branch")


def test_publish_github_execution_does_not_create_repo_when_prepare_worktree_fails(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return 0

    monkeypatch.setattr(cli_module.publish_github_execute, "_prepare_worktree", lambda source_root, work_dir: 1)

    exit_code = run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
    )

    assert exit_code == 1
    assert calls == ["gh-auth-status", "gh-workflow-scope"]


def test_publish_github_execution_replaces_workflow_head_sha_placeholder(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    status_args = {}

    def fake_runner(step):
        if step.name == "github-status":
            status_args["args"] = step.args
        return 0

    assert run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=True,
        runner=fake_runner,
        release_sha_resolver=lambda work_dir: "newsha",
    ) == 0

    assert "--workflow-head-sha" in status_args["args"]
    assert "newsha" in status_args["args"]
    assert "__RALPH_RELEASE_SHA__" not in status_args["args"]


def test_publish_github_execution_plan_mode_does_not_run_steps(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    calls = []

    execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )
    exit_code = run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        execute=False,
        runner=lambda step: calls.append(step.name) or 0,
    )

    assert exit_code == 0
    assert calls == []
    assert execution.steps[0].name == "gh-auth-status"


def test_publish_github_execution_checks_workflow_after_install(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert [step.name for step in execution.steps][-2:] == ["verify-installed-templates", "github-status"]
    assert execution.steps[-1].args == (
        sys.executable,
        "-m",
        "ralph_automation.cli",
        "publish-github-status",
        "--remote-url",
        "https://github.com/example/ralph-automation.git",
        "--branch",
        "main",
        "--workflow-name",
        "test",
        "--require-workflow",
        "--wait-workflow",
        "--workflow-head-sha",
        "__RALPH_RELEASE_SHA__",
        "--check",
    )


def test_publish_github_execution_plan_output_quotes_paths_with_spaces(tmp_path, capsys):
    source = tmp_path / "source with spaces"
    _write_public_source(source)
    install_dir = source / ".tmp" / "install dir"

    exit_code = run_github_publish(
        source,
        "https://github.com/example/ralph-automation.git",
        install_dir,
        execute=False,
    )

    output = capsys.readouterr().out
    work_dir = source.resolve() / ".tmp" / "github-worktree"
    assert exit_code == 0
    assert f'(cd "{work_dir}") git init' in output
    assert f'--target "{install_dir.resolve()}"' in output


def test_publish_github_execution_uses_throwaway_worktree_for_git_steps(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)

    execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    git_steps = [step for step in execution.steps if step.name.startswith("git-") or step.name.startswith("push-")]
    assert git_steps
    assert all(step.cwd == source.resolve() / ".tmp" / "github-worktree" for step in git_steps)
    assert all(step.cwd != source.resolve() for step in git_steps)
    assert execution.steps[1].name == "gh-workflow-scope"
    assert execution.steps[2].name == "prepare-worktree"


def test_publish_github_execution_blocks_unsafe_or_non_empty_worktree(tmp_path):
    source = tmp_path / "source"
    _write_public_source(source)
    outside_worktree = tmp_path / "outside-worktree"
    non_empty_worktree = source / ".tmp" / "github-worktree"
    non_empty_worktree.mkdir(parents=True)
    (non_empty_worktree / "leftover.txt").write_text("stale", encoding="utf-8")

    outside_execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
        work_dir=outside_worktree,
    )
    non_empty_execution = build_github_execution(
        source,
        "https://github.com/example/ralph-automation.git",
        source / ".tmp" / "install",
    )

    assert "unsafe-github-work-dir" in {finding.kind for finding in outside_execution.findings}
    assert "github-work-dir-not-empty" in {finding.kind for finding in non_empty_execution.findings}


def test_publish_github_execute_cli_passes_work_dir(tmp_path, monkeypatch):
    captured = {}

    def fake_run(source, remote_url, install_dir, *, tag, branch, work_dir, execute):
        captured["source"] = source
        captured["remote_url"] = remote_url
        captured["install_dir"] = install_dir
        captured["tag"] = tag
        captured["branch"] = branch
        captured["work_dir"] = work_dir
        captured["execute"] = execute
        return 0

    monkeypatch.setattr(cli_module.publish_github_execute, "run_github_publish", fake_run)

    assert main(
        [
            "publish-github-execute",
            "--source",
            str(tmp_path / "source"),
            "--remote-url",
            "https://github.com/example/ralph-automation.git",
            "--install-dir",
            str(tmp_path / "source" / ".tmp" / "install"),
            "--work-dir",
            str(tmp_path / "source" / ".tmp" / "work"),
            "--execute",
        ]
    ) == 0

    assert captured["work_dir"] == tmp_path / "source" / ".tmp" / "work"
    assert captured["execute"] is True
