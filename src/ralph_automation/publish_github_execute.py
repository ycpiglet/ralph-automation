from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .publish_bundle import build_bundle_plan
from .publish_check import PublishFinding
from .publish_github_plan import GitHubPlan
from .publish_github_plan import build_github_plan
from .publish_github_plan import _shell_quote


@dataclass(frozen=True)
class GitHubPublishStep:
    name: str
    args: tuple[str, ...]
    cwd: Path | None = None


@dataclass(frozen=True)
class GitHubPublishExecution:
    plan: GitHubPlan
    work_dir: Path
    steps: tuple[GitHubPublishStep, ...]
    findings: tuple[PublishFinding, ...] = ()


StepRunner = Callable[[GitHubPublishStep], int]
ReleaseShaResolver = Callable[[Path], str]
RELEASE_SHA_PLACEHOLDER = "__RALPH_RELEASE_SHA__"


def _verify_step(install_dir: Path) -> GitHubPublishStep:
    code = (
        "import sys; "
        f"sys.path.insert(0, {repr(str(install_dir.resolve()))}); "
        "from ralph_automation.sync import default_template_root; "
        "p=default_template_root(); "
        "sentinel=(p/'scripts'/'agent_worker.py').exists(); "
        "print(f'template_sentinel={sentinel}'); "
        "raise SystemExit(0 if sentinel else 1)"
    )
    return GitHubPublishStep("verify-installed-templates", (sys.executable, "-c", code))


def _github_status_step(remote_url: str, branch: str) -> GitHubPublishStep:
    return GitHubPublishStep(
        "github-status",
        (
            sys.executable,
            "-m",
            "ralph_automation.cli",
            "publish-github-status",
            "--remote-url",
            remote_url,
            "--branch",
            branch,
            "--workflow-name",
            "test",
            "--require-workflow",
            "--wait-workflow",
            "--workflow-head-sha",
            RELEASE_SHA_PLACEHOLDER,
            "--check",
        ),
    )


def _workflow_scope_step() -> GitHubPublishStep:
    code = (
        "import re, subprocess, sys; "
        "result=subprocess.run(['gh','auth','status'], text=True, capture_output=True, encoding='utf-8', errors='replace'); "
        "combined=(result.stdout or '')+'\\n'+(result.stderr or ''); "
        "sys.exit(result.returncode) if result.returncode != 0 else None; "
        "match=re.search(r'Token scopes:\\s*(.+)', combined, flags=re.IGNORECASE); "
        "scopes={part.strip().strip(\"'\\\"\").lower() for part in match.group(1).split(',')} if match else set(); "
        "missing=bool(match and 'workflow' not in scopes); "
        "print('gh-workflow-scope-missing: token lacks workflow scope') if missing else print('gh-workflow-scope=ok'); "
        "raise SystemExit(1 if missing else 0)"
    )
    return GitHubPublishStep("gh-workflow-scope", (sys.executable, "-c", code))


def _repo_ensure_step(repository: str) -> GitHubPublishStep:
    code = (
        "import json, subprocess, sys; "
        f"repo={repr(repository)}; "
        "view=subprocess.run(['gh','repo','view',repo,'--json','nameWithOwner,visibility,url'], text=True, capture_output=True); "
        "combined=(view.stdout or '')+(view.stderr or ''); "
        "lowered=combined.lower(); "
        "missing_repo=('not found' in lowered or 'could not resolve to a repository' in lowered); "
        "data=json.loads(view.stdout or '{}') if view.returncode == 0 else {}; "
        "visibility=str(data.get('visibility','')).upper(); "
        "sys.exit(0) if view.returncode == 0 and visibility == 'PUBLIC' else None; "
        "print(f'github-repo-not-public visibility={visibility or \"unknown\"}') if view.returncode == 0 else print(combined, end=''); "
        "sys.exit(1) if view.returncode == 0 else None; "
        "sys.exit(subprocess.run(['gh','repo','create',repo,'--public']).returncode) "
        "if missing_repo else sys.exit(view.returncode)"
    )
    return GitHubPublishStep("repo-ensure-public", (sys.executable, "-c", code))


def _prepare_worktree(source: Path, work_dir: Path) -> int:
    plan = build_bundle_plan(source, work_dir)
    if plan.findings:
        return 1
    for item in plan.files:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.target)
    return 0


def build_github_execution(
    source_root: Path,
    remote_url: str,
    install_dir: Path,
    *,
    tag: str = "v0.1.4",
    branch: str = "main",
    work_dir: Path | None = None,
) -> GitHubPublishExecution:
    plan = build_github_plan(source_root, remote_url, install_dir, tag=tag, branch=branch, work_dir=work_dir)
    publish_work_dir = plan.work_dir
    findings = tuple(plan.findings)
    steps: list[GitHubPublishStep] = []
    if not findings and plan.repository:
        steps.extend(
            [
                GitHubPublishStep("gh-auth-status", ("gh", "auth", "status")),
                _workflow_scope_step(),
                GitHubPublishStep("prepare-worktree", ("internal-copy-public-source", str(plan.source_root), str(publish_work_dir))),
                GitHubPublishStep("git-init", ("git", "init"), cwd=publish_work_dir),
                GitHubPublishStep("git-add", ("git", "add", "."), cwd=publish_work_dir),
                GitHubPublishStep(
                    "git-commit",
                    (
                        "git",
                        "-c",
                        "user.name=Ralph Release",
                        "-c",
                        "user.email=ralph-release@example.invalid",
                        "commit",
                        "-m",
                        f"release {tag}",
                    ),
                    cwd=publish_work_dir,
                ),
                GitHubPublishStep("git-branch", ("git", "branch", "-M", branch), cwd=publish_work_dir),
                GitHubPublishStep("git-remote-add", ("git", "remote", "add", "origin", remote_url), cwd=publish_work_dir),
                GitHubPublishStep("git-tag", ("git", "tag", tag), cwd=publish_work_dir),
                _repo_ensure_step(plan.repository),
                GitHubPublishStep("push-branch", ("git", "push", "-u", "origin", branch), cwd=publish_work_dir),
                GitHubPublishStep("push-tag", ("git", "push", "origin", tag), cwd=publish_work_dir),
                GitHubPublishStep(
                    "pip-install",
                    (
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--target",
                        str(plan.install_dir),
                        plan.install_spec,
                        "--no-deps",
                        "--no-build-isolation",
                        "--no-cache-dir",
                    ),
                ),
                _verify_step(plan.install_dir),
                _github_status_step(remote_url, branch),
            ]
        )
    return GitHubPublishExecution(plan=plan, work_dir=publish_work_dir, steps=tuple(steps), findings=findings)


def _default_runner(step: GitHubPublishStep) -> int:
    return subprocess.run(step.args, cwd=step.cwd, check=False).returncode


def _default_release_sha_resolver(work_dir: Path) -> str:
    result = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=work_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout.strip()


def _with_release_sha(step: GitHubPublishStep, release_sha: str) -> GitHubPublishStep:
    return GitHubPublishStep(
        step.name,
        tuple(release_sha if arg == RELEASE_SHA_PLACEHOLDER else arg for arg in step.args),
        cwd=step.cwd,
    )


def _render_step(step: GitHubPublishStep) -> str:
    prefix = f"(cd {_shell_quote(step.cwd)}) " if step.cwd else ""
    return prefix + " ".join(_shell_quote(arg) if any(char.isspace() for char in arg) else arg for arg in step.args)


def run_github_publish(
    source_root: Path,
    remote_url: str,
    install_dir: Path,
    *,
    tag: str = "v0.1.4",
    branch: str = "main",
    work_dir: Path | None = None,
    execute: bool,
    runner: StepRunner | None = None,
    release_sha_resolver: ReleaseShaResolver | None = None,
) -> int:
    execution = build_github_execution(source_root, remote_url, install_dir, tag=tag, branch=branch, work_dir=work_dir)
    print("# Ralph GitHub Publish Execute")
    print("")
    print(f"source={execution.plan.source_root}")
    print(f"work_dir={execution.work_dir}")
    print(f"remote_url={execution.plan.remote_url}")
    print(f"repository={execution.plan.repository}")
    print(f"tag={execution.plan.tag}")
    print(f"execute={str(execute).lower()}")
    print(f"findings={len(execution.findings)}")
    if execution.findings:
        print("")
        print("## Findings")
        print("")
        for finding in execution.findings:
            print(f"- `{finding.path}` {finding.kind}: {finding.detail}")
        return 1
    if not execute:
        print("")
        print("## Planned Steps")
        print("")
        for step in execution.steps:
            print(f"- `{step.name}` {_render_step(step)}")
        return 0

    active_runner = runner or _default_runner
    active_release_sha_resolver = release_sha_resolver or _default_release_sha_resolver
    release_sha: str | None = None
    for step in execution.steps:
        if step.name == "github-status" and RELEASE_SHA_PLACEHOLDER in step.args and (runner is None or release_sha_resolver):
            try:
                release_sha = release_sha or active_release_sha_resolver(execution.work_dir)
            except RuntimeError as exc:
                print("failed_step=resolve-release-sha")
                print(f"detail={exc}")
                return 1
            step = _with_release_sha(step, release_sha)
        print(f"$ {_render_step(step)}")
        code = _prepare_worktree(execution.plan.source_root, execution.work_dir) if step.name == "prepare-worktree" else active_runner(step)
        if code != 0:
            print(f"failed_step={step.name}")
            print(f"exit_code={code}")
            return code
    print("publish=complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Owner-approved public GitHub publish sequence")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Clean public source root")
    parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    parser.add_argument("--install-dir", type=Path, required=True, help="Temporary install verification target")
    parser.add_argument("--work-dir", type=Path, help="Temporary git worktree target; defaults to <source>/.tmp/github-worktree")
    parser.add_argument("--tag", default="v0.1.4", help="Release tag to push and verify")
    parser.add_argument("--branch", default="main", help="Branch to push")
    parser.add_argument("--execute", action="store_true", help="Actually run public GitHub create/push/tag/install commands")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_github_publish(
        args.source,
        args.remote_url,
        args.install_dir,
        tag=args.tag,
        branch=args.branch,
        work_dir=args.work_dir,
        execute=args.execute,
    )


if __name__ == "__main__":
    raise SystemExit(main())
