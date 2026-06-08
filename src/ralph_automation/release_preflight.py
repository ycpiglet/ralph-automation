from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .host_update import build_update_execution
from .host_update import build_update_plan
from .lock import build_lock_plan
from .publish_bundle import build_bundle_plan
from .publish_check import PublishFinding, analyze as analyze_publish
from .publish_github_plan import build_github_plan
from .publish_tag_smoke import build_tag_smoke_plan
from .sanitize import analyze as analyze_sanitize
from .sync import build_sync_plan


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    detail: str
    findings: tuple[PublishFinding, ...] = ()


@dataclass(frozen=True)
class PreflightPlan:
    source_root: Path
    host_root: Path
    remote_url: str
    tag: str
    checks: tuple[PreflightCheck, ...]

    @property
    def findings_count(self) -> int:
        return sum(len(check.findings) for check in self.checks)


def _status(findings: tuple[PublishFinding, ...] | list[PublishFinding]) -> str:
    return "blocked" if findings else "ok"


def _host_upstream_match_findings(update_plan, remote_url: str, tag: str) -> tuple[PublishFinding, ...]:
    config = update_plan.config
    findings: list[PublishFinding] = []
    if config.upstream_remote_url != remote_url:
        findings.append(
            PublishFinding(
                "ralph.yml",
                "upstream-remote-url-mismatch",
                "host upstream.remote_url must match release preflight remote_url",
            )
        )
    if config.upstream_ref != tag:
        findings.append(
            PublishFinding(
                "ralph.yml",
                "upstream-ref-mismatch",
                "host upstream.ref must match release preflight tag",
            )
        )
    return tuple(findings)


def _host_sync_findings(sync_plan) -> tuple[PublishFinding, ...]:
    return tuple(
        PublishFinding(
            conflict.path,
            "host-sync-conflict",
            "host file diverged from the locked managed file and would block sync",
        )
        for conflict in sync_plan.conflicts
    )


def _source_work_dir(source_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def build_preflight_plan(
    *,
    source_root: Path,
    host_root: Path,
    bundle_dir: Path,
    tag_repo_dir: Path,
    tag_install_dir: Path,
    github_install_dir: Path,
    host_install_dir: Path,
    remote_url: str,
    tag: str,
) -> PreflightPlan:
    source = source_root.resolve()
    host = host_root.resolve()
    resolved_bundle_dir = _source_work_dir(source, bundle_dir)
    resolved_tag_repo_dir = _source_work_dir(source, tag_repo_dir)
    resolved_tag_install_dir = _source_work_dir(source, tag_install_dir)
    resolved_host_install_dir = host_install_dir if host_install_dir.is_absolute() else host / host_install_dir
    resolved_github_install_dir = github_install_dir if github_install_dir.is_absolute() else source / github_install_dir

    sanitize_findings = tuple(analyze_sanitize(source))
    publish_findings = tuple(analyze_publish(source))
    bundle_plan = build_bundle_plan(source, resolved_bundle_dir)
    tag_plan = build_tag_smoke_plan(source, resolved_tag_repo_dir, resolved_tag_install_dir, tag)
    github_plan = build_github_plan(source, remote_url, resolved_github_install_dir, tag=tag)
    update_plan = build_update_plan(host, resolved_host_install_dir)
    if update_plan.findings:
        upstream_match_check = PreflightCheck("host-upstream-match", "skipped", "waiting-for-host-update-plan", ())
        update_command_check = PreflightCheck("host-update-command", "skipped", "waiting-for-host-update-plan", ())
        sync_check = PreflightCheck("host-sync-check", "skipped", "waiting-for-host-update-plan", ())
        lock_check = PreflightCheck("host-lock", "skipped", "waiting-for-host-update-plan", ())
    else:
        upstream_match_findings = _host_upstream_match_findings(update_plan, remote_url, tag)
        upstream_match_check = PreflightCheck(
            "host-upstream-match",
            _status(upstream_match_findings),
            "remote/ref match release inputs",
            upstream_match_findings,
        )
        update_execution = build_update_execution(host, resolved_host_install_dir, mode="check")
        if upstream_match_findings:
            update_command_check = PreflightCheck("host-update-command", "skipped", "waiting-for-host-upstream-match", ())
            sync_check = PreflightCheck("host-sync-check", "skipped", "waiting-for-host-upstream-match", ())
            lock_check = PreflightCheck("host-lock", "skipped", "waiting-for-host-upstream-match", ())
        else:
            update_command_check = PreflightCheck(
                "host-update-command",
                _status(update_execution.findings),
                f"steps={len(update_execution.steps)}",
                tuple(update_execution.findings),
            )
            sync_plan = build_sync_plan(host, template_root=source / "src" / "ralph_automation" / "templates" / "project")
            sync_findings = _host_sync_findings(sync_plan)
            sync_check = PreflightCheck(
                "host-sync-check",
                _status(sync_findings),
                f"updates={len(sync_plan.updates)} conflicts={len(sync_plan.conflicts)}",
                sync_findings,
            )
            lock_plan = build_lock_plan(host, template_root=source / "src" / "ralph_automation" / "templates" / "project")
            lock_detail = f"template_digest={lock_plan.record['installed']['template_digest']}"
            lock_check = PreflightCheck("host-lock", _status(lock_plan.findings), lock_detail, tuple(lock_plan.findings))

    checks = (
        PreflightCheck("sanitize", _status(sanitize_findings), f"findings={len(sanitize_findings)}", sanitize_findings),
        PreflightCheck("publish-check", _status(publish_findings), f"findings={len(publish_findings)}", publish_findings),
        PreflightCheck(
            "publish-bundle",
            _status(bundle_plan.findings),
            f"files={len(bundle_plan.files)}",
            tuple(bundle_plan.findings),
        ),
        PreflightCheck(
            "local-tag-smoke-plan",
            _status(tag_plan.findings),
            f"install_spec={tag_plan.install_spec}",
            tuple(tag_plan.findings),
        ),
        PreflightCheck(
            "github-publish-plan",
            _status(github_plan.findings),
            f"install_spec={github_plan.install_spec}",
            tuple(github_plan.findings),
        ),
        PreflightCheck(
            "host-update-plan",
            _status(update_plan.findings),
            f"install_spec={update_plan.install_spec}",
            tuple(update_plan.findings),
        ),
        upstream_match_check,
        update_command_check,
        sync_check,
        lock_check,
    )
    return PreflightPlan(source, host, remote_url, tag, checks)


def render(plan: PreflightPlan) -> str:
    lines = [
        "# Ralph Release Preflight",
        "",
        f"source={plan.source_root}",
        f"host_root={plan.host_root}",
        f"remote_url={plan.remote_url}",
        f"tag={plan.tag}",
        f"findings={plan.findings_count}",
        "",
        "| Check | Status | Detail | Findings |",
        "|-------|--------|--------|----------|",
    ]
    for check in plan.checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} | {len(check.findings)} |")
    if plan.findings_count:
        lines.extend(["", "## Findings", ""])
        for check in plan.checks:
            for finding in check.findings:
                lines.append(f"- {check.name}: `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def run_preflight(
    source_root: Path,
    host_root: Path,
    remote_url: str,
    *,
    bundle_dir: Path,
    tag_repo_dir: Path,
    tag_install_dir: Path,
    github_install_dir: Path,
    host_install_dir: Path,
    tag: str,
    check: bool,
) -> int:
    plan = build_preflight_plan(
        source_root=source_root,
        host_root=host_root,
        bundle_dir=bundle_dir,
        tag_repo_dir=tag_repo_dir,
        tag_install_dir=tag_install_dir,
        github_install_dir=github_install_dir,
        host_install_dir=host_install_dir,
        remote_url=remote_url,
        tag=tag,
    )
    print(render(plan))
    return 1 if check and plan.findings_count else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a non-mutating Ralph release readiness preflight")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Package source root")
    parser.add_argument("--host-root", type=Path, default=Path.cwd(), help="Host project root")
    parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish/install from")
    parser.add_argument("--tag", default="v0.1.4", help="Release tag")
    parser.add_argument("--bundle-dir", type=Path, default=Path(".tmp/public-source"), help="Temporary publish bundle dir")
    parser.add_argument("--tag-repo-dir", type=Path, default=Path(".tmp/tag-repo"), help="Temporary local tag repo dir")
    parser.add_argument("--tag-install-dir", type=Path, default=Path(".tmp/tag-install"), help="Temporary local tag install dir")
    parser.add_argument("--github-install-dir", type=Path, default=Path(".tmp/github-install"), help="Temporary GitHub tag install dir")
    parser.add_argument("--host-install-dir", type=Path, default=Path(".tmp/ralph-upstream"), help="Temporary host upstream install dir")
    parser.add_argument("--check", action="store_true", help="Fail if any preflight finding exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_preflight(
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


if __name__ == "__main__":
    raise SystemExit(main())
