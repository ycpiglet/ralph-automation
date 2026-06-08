from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from .publish_bundle import SKIP_SUFFIXES
from .publish_bundle import _source_files
from .publish_check import _ignored_by_package_gitignore
from .publish_check import PublishFinding, analyze as analyze_publish
from .sanitize import analyze as analyze_sanitize


@dataclass(frozen=True)
class GitHubPlan:
    source_root: Path
    remote_url: str
    repository: str
    install_dir: Path
    work_dir: Path
    tag: str
    branch: str
    install_spec: str
    commands: tuple[str, ...]
    findings: tuple[PublishFinding, ...] = ()


def _is_github_remote(remote_url: str) -> bool:
    return remote_url.startswith("https://github.com/") or remote_url.startswith("git@github.com:")


def _parse_github_repository(remote_url: str) -> str | None:
    if remote_url.startswith("https://github.com/"):
        path = remote_url.removeprefix("https://github.com/")
    elif remote_url.startswith("git@github.com:"):
        path = remote_url.removeprefix("git@github.com:")
    else:
        return None

    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, repo = parts
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
        return None
    return f"{parts[0]}/{parts[1]}"


PLACEHOLDER_REMOTE_OWNERS = {"OWNER", "USER", "ORG", "ORGANIZATION", "<OWNER>", "<USER>", "<ORG>"}


def _remote_placeholder_finding(repository: str | None) -> PublishFinding | None:
    if not repository:
        return None
    owner = repository.split("/", 1)[0].strip()
    normalized = owner.upper()
    if normalized in PLACEHOLDER_REMOTE_OWNERS:
        return PublishFinding("remote-url", "placeholder-remote-url", "replace placeholder GitHub owner before release")
    return None


def _install_spec(remote_url: str, tag: str) -> str:
    if remote_url.startswith("git@github.com:"):
        path = remote_url.removeprefix("git@github.com:")
        return f"git+ssh://git@github.com/{path}@{tag}"
    return f"git+{remote_url}@{tag}"


def _python_literal(path: Path) -> str:
    return repr(str(path.resolve()))


def _python_string(value: str | Path) -> str:
    return repr(str(value))


def _shell_quote(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def _safe_git_ref(value: str) -> bool:
    if not value or value.startswith(("-", "/", ".")) or value.endswith(("/", ".")):
        return False
    if ".." in value or "@{" in value or "\\" in value:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._/-]+", value))


def _conditional_repo_command(repository: str) -> str:
    code = (
        "import json, subprocess, sys; "
        f"repo={_python_string(repository)}; "
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
    return f"python -c {_shell_quote(code)}"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _publishable_existing_files(source: Path) -> set[str]:
    files: set[str] = set()
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(source).parts
        if rel_parts[0] == ".git":
            continue
        if rel_parts[0] in {".tmp", "build", "dist"}:
            continue
        if rel_parts[0] == "templates" and _ignored_by_package_gitignore(source, "/templates/"):
            continue
        if any(part in {"__pycache__", ".pytest_cache"} for part in rel_parts):
            continue
        if len(rel_parts) > 1 and rel_parts[0] == "src" and rel_parts[1].endswith(".egg-info"):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.add(path.relative_to(source).as_posix())
    return files


def _github_install_dir_findings(source: Path, install: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    allowed_root = source / ".tmp"
    if install == source or not _is_relative_to(install, allowed_root):
        findings.append(
            PublishFinding(
                "install-dir",
                "unsafe-github-install-dir",
                "GitHub install verification dir must be under source .tmp/",
            )
        )
    if install.exists() and any(install.iterdir()):
        findings.append(
            PublishFinding(
                "install-dir",
                "github-install-dir-not-empty",
                "GitHub install verification dir must be empty before publish execution",
            )
        )
    return findings


def _github_work_dir_findings(source: Path, work_dir: Path, install_dir: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    allowed_root = source / ".tmp"
    if work_dir == source or not _is_relative_to(work_dir, allowed_root):
        findings.append(
            PublishFinding(
                "work-dir",
                "unsafe-github-work-dir",
                "GitHub publish work dir must be under source .tmp/",
            )
        )
    if work_dir == install_dir or _is_relative_to(install_dir, work_dir) or _is_relative_to(work_dir, install_dir):
        findings.append(
            PublishFinding(
                "work-dir",
                "github-work-dir-overlaps-install-dir",
                "GitHub publish work dir must not overlap the install verification dir",
            )
        )
    if work_dir.exists() and any(work_dir.iterdir()):
        findings.append(
            PublishFinding(
                "work-dir",
                "github-work-dir-not-empty",
                "GitHub publish work dir must be empty before publish execution",
            )
        )
    return findings


def build_github_plan(
    source_root: Path,
    remote_url: str,
    install_dir: Path,
    *,
    tag: str = "v0.1.0",
    branch: str = "main",
    work_dir: Path | None = None,
) -> GitHubPlan:
    source = source_root.resolve()
    install = install_dir.resolve()
    work = work_dir.resolve() if work_dir else source / ".tmp" / "github-worktree"
    findings: list[PublishFinding] = []

    if (source / ".git").exists():
        findings.append(
            PublishFinding("source", "source-git-repo-exists", "publish source must be a fresh clean bundle without .git")
        )
    findings.extend(_github_install_dir_findings(source, install))
    findings.extend(_github_work_dir_findings(source, work, install))
    findings.extend(analyze_publish(source))
    findings.extend(
        PublishFinding(finding.path, f"sanitize:{finding.kind}", finding.detail)
        for finding in analyze_sanitize(source)
    )
    expected_public_files = {path.relative_to(source).as_posix() for path in _source_files(source)}
    for rel in sorted(_publishable_existing_files(source) - expected_public_files):
        findings.append(
            PublishFinding(
                rel,
                "unexpected-source-file",
                "clean GitHub source must come from publish-bundle selected files only",
            )
        )
    repository = _parse_github_repository(remote_url)
    is_github_remote = _is_github_remote(remote_url)
    if not is_github_remote:
        findings.append(
            PublishFinding("remote-url", "non-github-remote-url", "remote must be a GitHub HTTPS or SSH URL")
        )
    elif repository is None:
        findings.append(
            PublishFinding(
                "remote-url",
                "malformed-github-remote-url",
                "remote must include owner and repository name",
            )
        )
    placeholder_finding = _remote_placeholder_finding(repository)
    if placeholder_finding:
        findings.append(placeholder_finding)
    if not _safe_git_ref(branch):
        findings.append(PublishFinding("branch", "unsafe-git-branch", "branch must be a simple git ref name"))
    if not _safe_git_ref(tag):
        findings.append(PublishFinding("tag", "unsafe-git-tag", "tag must be a simple git ref name"))

    install_spec = _install_spec(remote_url, tag)
    workflow_status_code = (
        "import subprocess, sys; "
        f"sha=subprocess.check_output(['git','-C',{_python_string(work)},'rev-parse','HEAD'], text=True).strip(); "
        "from ralph_automation.cli import main; "
        "raise SystemExit(main(["
        "'publish-github-status',"
        f"'--remote-url',{_python_string(remote_url)},"
        f"'--branch',{_python_string(branch)},"
        "'--workflow-name','test',"
        "'--require-workflow','--wait-workflow','--workflow-head-sha',sha,'--check'"
        "]))"
    )
    repository_commands = (_conditional_repo_command(repository),) if repository else ()
    commands = (
        f"python -m ralph_automation.cli publish-bundle --source {_shell_quote(source)} --dest {_shell_quote(work)} --apply",
        f"cd {_shell_quote(work)} && git init",
        f"cd {_shell_quote(work)} && git add .",
        f'cd {_shell_quote(work)} && git -c user.name="Ralph Release" -c user.email=ralph-release@example.invalid commit -m "release {tag}"',
        f"cd {_shell_quote(work)} && git branch -M {_shell_quote(branch)}",
        f"cd {_shell_quote(work)} && git remote add origin {_shell_quote(remote_url)}",
        f"cd {_shell_quote(work)} && git tag {_shell_quote(tag)}",
    ) + repository_commands + (
        f"cd {_shell_quote(work)} && git push -u origin {_shell_quote(branch)}",
        f"cd {_shell_quote(work)} && git push origin {_shell_quote(tag)}",
        f"python -m pip install --target {_shell_quote(install)} {_shell_quote(install_spec)} --no-deps --no-build-isolation --no-cache-dir",
        (
            "python -c \"import sys; "
            f"sys.path.insert(0, {_python_literal(install)}); "
            "from ralph_automation.sync import default_template_root; "
            "p=default_template_root(); sentinel=(p/'scripts'/'agent_worker.py').exists(); "
            "print(f'template_sentinel={sentinel}'); raise SystemExit(0 if sentinel else 1)\""
        ),
        f"python -c {_shell_quote(workflow_status_code)}",
    )
    return GitHubPlan(source, remote_url, repository or "", install, work, tag, branch, install_spec, commands, tuple(findings))


def render(plan: GitHubPlan) -> str:
    lines = [
        "# Ralph GitHub Publish Plan",
        "",
        f"source={plan.source_root}",
        f"remote_url={plan.remote_url}",
        f"repository={plan.repository}",
        f"branch={plan.branch}",
        f"tag={plan.tag}",
        f"install_dir={plan.install_dir}",
        f"work_dir={plan.work_dir}",
        f"install_spec={plan.install_spec}",
        f"findings={len(plan.findings)}",
        "",
        "## Owner-approved external commands",
        "",
    ]
    for command in plan.commands:
        lines.append(f"- `{command}`")
    if plan.findings:
        lines.extend(["", "## Findings", ""])
        for finding in plan.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def run_github_plan(
    source_root: Path,
    remote_url: str,
    install_dir: Path,
    *,
    tag: str,
    branch: str,
    work_dir: Path | None,
    check: bool,
) -> int:
    plan = build_github_plan(source_root, remote_url, install_dir, tag=tag, branch=branch, work_dir=work_dir)
    print(render(plan))
    return 1 if check and plan.findings else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan the Owner-approved public GitHub publish step")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Clean public source root")
    parser.add_argument("--remote-url", required=True, help="GitHub remote URL to publish to")
    parser.add_argument("--install-dir", type=Path, required=True, help="Temporary install verification target")
    parser.add_argument("--work-dir", type=Path, help="Temporary git worktree target; defaults to <source>/.tmp/github-worktree")
    parser.add_argument("--tag", default="v0.1.0", help="Release tag to push and verify")
    parser.add_argument("--branch", default="main", help="Branch to push")
    parser.add_argument("--check", action="store_true", help="Report plan and fail if readiness findings exist")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_github_plan(
        args.source,
        args.remote_url,
        args.install_dir,
        tag=args.tag,
        branch=args.branch,
        work_dir=args.work_dir,
        check=args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
