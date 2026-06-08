from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .publish_check import PublishFinding
from .publish_github_plan import _parse_github_repository


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitHubStatusCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class GitHubPublishStatus:
    remote_url: str
    repository: str
    checks: tuple[GitHubStatusCheck, ...]
    findings: tuple[PublishFinding, ...] = ()


StatusRunner = Callable[[tuple[str, ...]], CommandResult]
Sleeper = Callable[[float], None]


def _default_runner(args: tuple[str, ...]) -> CommandResult:
    result = subprocess.run(args, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return CommandResult(args=args, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _first_line(result: CommandResult) -> str:
    text = (result.stderr or result.stdout).strip()
    if not text:
        return f"exit {result.returncode}"
    lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in ("failed", "invalid", "requires authentication", "not found")):
            return line
    return lines[0]


def _token_scopes(result: CommandResult) -> set[str] | None:
    combined = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"Token scopes:\s*(.+)", combined, flags=re.IGNORECASE)
    if not match:
        return None
    return {part.strip().strip("'\"").lower() for part in match.group(1).split(",") if part.strip()}


def _workflow_status(
    repository: str,
    branch: str,
    workflow_name: str,
    runner: StatusRunner,
    *,
    workflow_head_sha: str | None = None,
) -> tuple[GitHubStatusCheck, PublishFinding | None]:
    result = runner(
        (
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--branch",
            branch,
            "--workflow",
            workflow_name,
            "--limit",
            "1",
            "--json",
            "status,conclusion,headSha,url,workflowName",
        )
    )
    if result.returncode != 0:
        detail = _first_line(result)
        return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
            "gh", "github-workflow-unavailable", detail
        )
    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        detail = f"invalid workflow JSON: {exc}"
        return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
            "gh", "github-workflow-unavailable", detail
        )
    if not runs:
        detail = f"no workflow runs found for branch={branch}"
        return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
            "gh", "github-workflow-missing", detail
        )

    latest = runs[0]
    status = str(latest.get("status", ""))
    conclusion = str(latest.get("conclusion", ""))
    head_sha = str(latest.get("headSha", ""))
    actual_workflow_name = str(latest.get("workflowName", ""))
    detail = (
        f"status={status} conclusion={conclusion} "
        f"workflow={actual_workflow_name} headSha={head_sha} url={latest.get('url', '')}"
    )
    if workflow_name and actual_workflow_name and actual_workflow_name != workflow_name:
        detail = f"expected_workflow={workflow_name} {detail}"
        return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
            "gh", "github-workflow-wrong-name", detail
        )
    if workflow_head_sha and head_sha != workflow_head_sha:
        detail = f"expected_headSha={workflow_head_sha} {detail}"
        return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
            "gh", "github-workflow-head-sha-mismatch", detail
        )
    if status == "completed" and conclusion == "success":
        return GitHubStatusCheck("workflow", "ok", detail), None
    return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
        "gh", "github-workflow-not-success", detail
    )


def _wait_for_workflow_status(
    repository: str,
    branch: str,
    workflow_name: str,
    runner: StatusRunner,
    *,
    workflow_head_sha: str | None,
    timeout_seconds: float,
    poll_seconds: float,
    sleeper: Sleeper,
) -> tuple[GitHubStatusCheck, PublishFinding | None]:
    started = time.monotonic()
    last_check, last_finding = _workflow_status(repository, branch, workflow_name, runner, workflow_head_sha=workflow_head_sha)
    retry_kinds = {"github-workflow-not-success", "github-workflow-head-sha-mismatch", "github-workflow-missing", "github-workflow-wrong-name"}
    while last_finding and last_finding.kind in retry_kinds:
        if time.monotonic() - started >= timeout_seconds:
            detail = f"timed out after {timeout_seconds:g}s waiting for workflow success; last={last_check.detail}"
            return GitHubStatusCheck("workflow", "blocked", detail), PublishFinding(
                "gh", "github-workflow-timeout", detail
            )
        if poll_seconds > 0:
            sleeper(poll_seconds)
        last_check, last_finding = _workflow_status(repository, branch, workflow_name, runner, workflow_head_sha=workflow_head_sha)
    return last_check, last_finding


def build_github_status(
    remote_url: str,
    *,
    branch: str = "main",
    workflow_name: str = "test",
    require_workflow: bool = False,
    wait_workflow: bool = False,
    workflow_head_sha: str | None = None,
    workflow_timeout_seconds: float = 300,
    workflow_poll_seconds: float = 5,
    sleeper: Sleeper = time.sleep,
    runner: StatusRunner | None = None,
) -> GitHubPublishStatus:
    active_runner = runner or _default_runner
    repository = _parse_github_repository(remote_url) or ""
    checks: list[GitHubStatusCheck] = []
    findings: list[PublishFinding] = []

    if not repository:
        findings.append(
            PublishFinding("remote-url", "malformed-github-remote-url", "remote must include owner and repository name")
        )

    auth = active_runner(("gh", "auth", "status"))
    if auth.returncode != 0:
        detail = _first_line(auth)
        checks.append(GitHubStatusCheck("auth", "blocked", detail))
        findings.append(PublishFinding("gh", "gh-auth-unavailable", detail))
        return GitHubPublishStatus(remote_url, repository, tuple(checks), tuple(findings))
    checks.append(GitHubStatusCheck("auth", "ok", "authenticated"))

    scopes = _token_scopes(auth)
    if scopes is not None:
        if "workflow" not in scopes:
            detail = "missing workflow scope"
            checks.append(GitHubStatusCheck("scope", "blocked", detail))
            findings.append(PublishFinding("gh", "gh-workflow-scope-missing", detail))
        else:
            checks.append(GitHubStatusCheck("scope", "ok", "workflow scope present"))

    user = active_runner(("gh", "api", "user", "--jq", ".login"))
    if user.returncode != 0:
        detail = _first_line(user)
        checks.append(GitHubStatusCheck("user", "blocked", detail))
        findings.append(PublishFinding("gh", "gh-user-unavailable", detail))
    else:
        checks.append(GitHubStatusCheck("user", "ok", f"login={user.stdout.strip()}"))

    if repository:
        repo = active_runner(("gh", "repo", "view", repository, "--json", "nameWithOwner,visibility,url"))
        if repo.returncode != 0:
            detail = _first_line(repo)
            checks.append(GitHubStatusCheck("repo", "blocked", detail))
            findings.append(PublishFinding("gh", "github-repo-unavailable", detail))
        else:
            try:
                repo_data = json.loads(repo.stdout or "{}")
            except json.JSONDecodeError:
                repo_data = {}
            visibility = str(repo_data.get("visibility", ""))
            if not visibility:
                detail = "visibility=missing"
                checks.append(GitHubStatusCheck("repo", "blocked", detail))
                findings.append(PublishFinding("gh", "github-repo-visibility-missing", detail))
            elif visibility.upper() != "PUBLIC":
                detail = f"visibility={visibility}"
                checks.append(GitHubStatusCheck("repo", "blocked", detail))
                findings.append(PublishFinding("gh", "github-repo-not-public", detail))
            else:
                checks.append(GitHubStatusCheck("repo", "ok", "available"))

    if require_workflow and repository and not any(finding.kind == "github-repo-unavailable" for finding in findings):
        if wait_workflow:
            workflow_check, workflow_finding = _wait_for_workflow_status(
                repository,
                branch,
                workflow_name,
                active_runner,
                workflow_head_sha=workflow_head_sha,
                timeout_seconds=workflow_timeout_seconds,
                poll_seconds=workflow_poll_seconds,
                sleeper=sleeper,
            )
        else:
            workflow_check, workflow_finding = _workflow_status(
                repository,
                branch,
                workflow_name,
                active_runner,
                workflow_head_sha=workflow_head_sha,
            )
        checks.append(workflow_check)
        if workflow_finding:
            findings.append(workflow_finding)

    return GitHubPublishStatus(remote_url, repository, tuple(checks), tuple(findings))


def render(status: GitHubPublishStatus) -> str:
    lines = [
        "# Ralph GitHub Publish Status",
        "",
        f"remote_url={status.remote_url}",
        f"repository={status.repository}",
        f"findings={len(status.findings)}",
        "",
        "| Check | Status | Detail |",
        "|-------|--------|--------|",
    ]
    for check in status.checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    if status.findings:
        lines.extend(["", "## Findings", ""])
        for finding in status.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def run_github_status(
    remote_url: str,
    *,
    branch: str,
    workflow_name: str,
    require_workflow: bool,
    wait_workflow: bool,
    workflow_head_sha: str | None,
    workflow_timeout_seconds: float,
    workflow_poll_seconds: float,
    check: bool,
) -> int:
    status = build_github_status(
        remote_url,
        branch=branch,
        workflow_name=workflow_name,
        require_workflow=require_workflow,
        wait_workflow=wait_workflow,
        workflow_head_sha=workflow_head_sha,
        workflow_timeout_seconds=workflow_timeout_seconds,
        workflow_poll_seconds=workflow_poll_seconds,
    )
    print(render(status))
    return 1 if check and status.findings else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only check for GitHub publish readiness")
    parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    parser.add_argument("--branch", default="main", help="Branch whose latest workflow run should be checked")
    parser.add_argument("--workflow-name", default="test", help="Workflow name to require for release evidence")
    parser.add_argument("--require-workflow", action="store_true", help="Require latest branch workflow run to be successful")
    parser.add_argument("--wait-workflow", action="store_true", help="Poll until the latest branch workflow succeeds or times out")
    parser.add_argument("--workflow-head-sha", help="Require the workflow run to match this head SHA")
    parser.add_argument("--workflow-timeout-seconds", type=float, default=300, help="Max seconds to wait for workflow success")
    parser.add_argument("--workflow-poll-seconds", type=float, default=5, help="Seconds between workflow status polls")
    parser.add_argument("--check", action="store_true", help="Fail if GitHub auth/repo status is not ready")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_github_status(
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


if __name__ == "__main__":
    raise SystemExit(main())
