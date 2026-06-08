from __future__ import annotations

import argparse
import sys

from . import __version__
from . import exporter
from . import host_update
from . import inventory
from . import lock
from . import publish_bundle
from . import publish_check
from . import publish_github_execute
from . import publish_github_plan
from . import publish_github_status
from . import publish_tag_smoke
from . import release_preflight
from . import sanitize
from . import sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ralph", description="Ralph automation core CLI")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    subparsers = parser.add_subparsers(dest="command")

    inventory_parser = subparsers.add_parser("inventory", help="Classify migration export candidates")
    inventory_parser.add_argument("--root", type=inventory.Path, default=inventory.Path.cwd(), help="Repo root")
    inventory_parser.add_argument("--json", action="store_true", help="Emit machine-readable inventory")
    inventory_parser.add_argument("--check", action="store_true", help="Fail if unsafe paths are export candidates")
    inventory_parser.add_argument("--limit", type=int, default=20, help="Text report export-candidate sample size")

    sync_parser = subparsers.add_parser("sync", help="Check/diff/apply host template updates")
    sync_parser.add_argument("--root", type=sync.Path, default=sync.Path.cwd(), help="Host project root")
    sync_parser.add_argument("--template-root", type=sync.Path, default=None, help="Template root override")
    mode = sync_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report available updates without writing")
    mode.add_argument("--diff", action="store_true", help="Show exact template changes")
    mode.add_argument("--apply", action="store_true", help="Apply safe selected updates")

    sanitize_parser = subparsers.add_parser("sanitize", help="Check staged public package content")
    sanitize_parser.add_argument("--root", type=sanitize.Path, default=sanitize.Path.cwd(), help="Package root")
    sanitize_parser.add_argument("--check", action="store_true", help="Fail if public sanitization findings exist")

    export_parser = subparsers.add_parser("export", help="Stage host automation candidates into the public package")
    export_parser.add_argument("--host-root", type=exporter.Path, default=exporter.Path.cwd(), help="Host project root")
    export_parser.add_argument("--package-root", type=exporter.Path, default=None, help="Ralph package root")
    export_mode = export_parser.add_mutually_exclusive_group(required=True)
    export_mode.add_argument("--check", action="store_true", help="Report export readiness without writing")
    export_mode.add_argument("--diff", action="store_true", help="Show exact template staging changes")
    export_mode.add_argument("--apply", action="store_true", help="Copy missing safe templates into the package")

    publish_parser = subparsers.add_parser("publish-check", help="Check public GitHub publication readiness")
    publish_parser.add_argument("--root", type=publish_check.Path, default=publish_check.Path.cwd(), help="Package root")
    publish_parser.add_argument("--check", action="store_true", help="Fail if publish-readiness findings exist")

    bundle_parser = subparsers.add_parser("publish-bundle", help="Create a clean public GitHub source bundle")
    bundle_parser.add_argument("--source", type=publish_bundle.Path, default=publish_bundle.Path.cwd(), help="Package source root")
    bundle_parser.add_argument("--dest", type=publish_bundle.Path, required=True, help="Destination directory")
    bundle_mode = bundle_parser.add_mutually_exclusive_group(required=True)
    bundle_mode.add_argument("--check", action="store_true", help="Report selected files and findings without writing")
    bundle_mode.add_argument("--apply", action="store_true", help="Copy clean public source files into dest")

    tag_smoke_parser = subparsers.add_parser("publish-tag-smoke", help="Smoke-test install from a local git tag")
    tag_smoke_parser.add_argument("--source", type=publish_tag_smoke.Path, default=publish_tag_smoke.Path.cwd(), help="Package source root")
    tag_smoke_parser.add_argument("--repo-dir", type=publish_tag_smoke.Path, required=True, help="Temporary git repo directory")
    tag_smoke_parser.add_argument("--install-dir", type=publish_tag_smoke.Path, required=True, help="Temporary pip target directory")
    tag_smoke_parser.add_argument("--tag", default="v0.1.0", help="Local tag to create and install from")
    tag_smoke_mode = tag_smoke_parser.add_mutually_exclusive_group(required=True)
    tag_smoke_mode.add_argument("--check", action="store_true", help="Report smoke plan without writing")
    tag_smoke_mode.add_argument("--apply", action="store_true", help="Create local tag and install from it")

    github_plan_parser = subparsers.add_parser("publish-github-plan", help="Plan the public GitHub publish step")
    github_plan_parser.add_argument("--source", type=publish_github_plan.Path, default=publish_github_plan.Path.cwd(), help="Clean public source root")
    github_plan_parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    github_plan_parser.add_argument("--install-dir", type=publish_github_plan.Path, required=True, help="Temporary install verification target")
    github_plan_parser.add_argument("--work-dir", type=publish_github_plan.Path, help="Temporary git worktree target")
    github_plan_parser.add_argument("--tag", default="v0.1.0", help="Release tag to push and verify")
    github_plan_parser.add_argument("--branch", default="main", help="Branch to push")
    github_plan_parser.add_argument("--check", action="store_true", help="Report plan and fail if readiness findings exist")

    github_status_parser = subparsers.add_parser("publish-github-status", help="Read-only check for GitHub publish readiness")
    github_status_parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    github_status_parser.add_argument("--branch", default="main", help="Branch whose latest workflow run should be checked")
    github_status_parser.add_argument("--workflow-name", default="test", help="Workflow name to require for release evidence")
    github_status_parser.add_argument("--require-workflow", action="store_true", help="Require latest branch workflow run to be successful")
    github_status_parser.add_argument("--wait-workflow", action="store_true", help="Poll until the latest branch workflow succeeds or times out")
    github_status_parser.add_argument("--workflow-head-sha", help="Require the workflow run to match this head SHA")
    github_status_parser.add_argument("--workflow-timeout-seconds", type=float, default=300, help="Max seconds to wait for workflow success")
    github_status_parser.add_argument("--workflow-poll-seconds", type=float, default=5, help="Seconds between workflow status polls")
    github_status_parser.add_argument("--check", action="store_true", help="Fail if GitHub auth/repo status is not ready")

    github_execute_parser = subparsers.add_parser("publish-github-execute", help="Run the Owner-approved public GitHub publish sequence")
    github_execute_parser.add_argument("--source", type=publish_github_execute.Path, default=publish_github_execute.Path.cwd(), help="Clean public source root")
    github_execute_parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    github_execute_parser.add_argument("--install-dir", type=publish_github_execute.Path, required=True, help="Temporary install verification target")
    github_execute_parser.add_argument("--work-dir", type=publish_github_execute.Path, help="Temporary git worktree target")
    github_execute_parser.add_argument("--tag", default="v0.1.0", help="Release tag to push and verify")
    github_execute_parser.add_argument("--branch", default="main", help="Branch to push")
    github_execute_parser.add_argument("--execute", action="store_true", help="Actually run public GitHub create/push/tag/install commands")

    update_plan_parser = subparsers.add_parser("update-plan", help="Plan host update from configured Ralph upstream")
    update_plan_parser.add_argument("--root", type=host_update.Path, default=host_update.Path.cwd(), help="Host project root")
    update_plan_parser.add_argument("--install-dir", type=host_update.Path, required=True, help="Temporary install target for upstream package")
    update_plan_parser.add_argument("--check", action="store_true", help="Fail if host upstream config is incomplete")

    update_parser = subparsers.add_parser("update", help="Install configured Ralph upstream and run host sync")
    update_parser.add_argument("--root", type=host_update.Path, default=host_update.Path.cwd(), help="Host project root")
    update_parser.add_argument("--install-dir", type=host_update.Path, required=True, help="Temporary install target for upstream package")
    update_mode = update_parser.add_mutually_exclusive_group(required=True)
    update_mode.add_argument("--check", action="store_true", help="Install upstream and report available host updates")
    update_mode.add_argument("--diff", action="store_true", help="Install upstream and show exact host update diff")
    update_mode.add_argument("--apply", action="store_true", help="Install upstream, apply safe updates, and write ralph.lock.json")

    lock_parser = subparsers.add_parser("lock", help="Check or write the host Ralph upstream lock")
    lock_parser.add_argument("--root", type=lock.Path, default=lock.Path.cwd(), help="Host project root")
    lock_parser.add_argument("--template-root", type=lock.Path, default=None, help="Template root override")
    lock_mode = lock_parser.add_mutually_exclusive_group(required=True)
    lock_mode.add_argument("--check", action="store_true", help="Fail if ralph.lock.json is missing or stale")
    lock_mode.add_argument("--write", action="store_true", help="Write ralph.lock.json for the current installed package")

    preflight_parser = subparsers.add_parser("release-preflight", help="Run Ralph release readiness checks")
    preflight_parser.add_argument("--source", type=release_preflight.Path, default=release_preflight.Path.cwd(), help="Package source root")
    preflight_parser.add_argument("--host-root", type=release_preflight.Path, default=release_preflight.Path.cwd(), help="Host project root")
    preflight_parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish/install from")
    preflight_parser.add_argument("--tag", default="v0.1.0", help="Release tag")
    preflight_parser.add_argument("--bundle-dir", type=release_preflight.Path, default=release_preflight.Path(".tmp/public-source"), help="Temporary publish bundle dir")
    preflight_parser.add_argument("--tag-repo-dir", type=release_preflight.Path, default=release_preflight.Path(".tmp/tag-repo"), help="Temporary local tag repo dir")
    preflight_parser.add_argument("--tag-install-dir", type=release_preflight.Path, default=release_preflight.Path(".tmp/tag-install"), help="Temporary local tag install dir")
    preflight_parser.add_argument("--github-install-dir", type=release_preflight.Path, default=release_preflight.Path(".tmp/github-install"), help="Temporary GitHub tag install dir")
    preflight_parser.add_argument("--host-install-dir", type=release_preflight.Path, default=release_preflight.Path(".tmp/ralph-upstream"), help="Temporary host upstream install dir")
    preflight_parser.add_argument("--check", action="store_true", help="Fail if any preflight finding exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.version:
        print(__version__)
        return 0
    if args.command == "inventory":
        return inventory.run_inventory(args.root, json_output=args.json, check=args.check, limit=args.limit)
    if args.command == "sync":
        if args.check:
            mode = "check"
        elif args.diff:
            mode = "diff"
        else:
            mode = "apply"
        return sync.run_sync(args.root, mode, template_root=args.template_root)
    if args.command == "sanitize":
        return sanitize.run_sanitize(args.root, check=args.check)
    if args.command == "export":
        if args.check:
            mode = "check"
        elif args.diff:
            mode = "diff"
        else:
            mode = "apply"
        return exporter.run_export(args.host_root, mode, package_root=args.package_root)
    if args.command == "publish-check":
        return publish_check.run_publish_check(args.root, check=args.check)
    if args.command == "publish-bundle":
        return publish_bundle.run_publish_bundle(args.source, args.dest, check=args.check, apply=args.apply)
    if args.command == "publish-tag-smoke":
        return publish_tag_smoke.run_tag_smoke(
            args.source,
            args.repo_dir,
            args.install_dir,
            args.tag,
            check=args.check,
            apply=args.apply,
        )
    if args.command == "publish-github-plan":
        return publish_github_plan.run_github_plan(
            args.source,
            args.remote_url,
            args.install_dir,
            tag=args.tag,
            branch=args.branch,
            work_dir=args.work_dir,
            check=args.check,
        )
    if args.command == "publish-github-status":
        return publish_github_status.run_github_status(
            args.remote_url,
            branch=args.branch,
            workflow_name=args.workflow_name,
            require_workflow=args.require_workflow,
            wait_workflow=args.wait_workflow,
            workflow_head_sha=args.workflow_head_sha,
            workflow_timeout_seconds=args.workflow_timeout_seconds,
            workflow_poll_seconds=args.workflow_poll_seconds,
            check=args.check,
        )
    if args.command == "publish-github-execute":
        return publish_github_execute.run_github_publish(
            args.source,
            args.remote_url,
            args.install_dir,
            tag=args.tag,
            branch=args.branch,
            work_dir=args.work_dir,
            execute=args.execute,
        )
    if args.command == "update-plan":
        return host_update.run_update_plan(args.root, args.install_dir, check=args.check)
    if args.command == "update":
        if args.check:
            mode = "check"
        elif args.diff:
            mode = "diff"
        else:
            mode = "apply"
        return host_update.run_update(args.root, args.install_dir, mode=mode)
    if args.command == "lock":
        return lock.run_lock(args.root, mode="write" if args.write else "check", template_root=args.template_root)
    if args.command == "release-preflight":
        return release_preflight.run_preflight(
            args.source,
            args.host_root,
            args.remote_url,
            bundle_dir=args.bundle_dir,
            tag_repo_dir=args.tag_repo_dir,
            tag_install_dir=args.tag_install_dir,
            github_install_dir=args.github_install_dir,
            host_install_dir=args.host_install_dir,
            tag=args.tag,
            check=args.check,
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
