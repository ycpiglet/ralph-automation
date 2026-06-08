from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .publish_bundle import build_bundle_plan
from .publish_check import PublishFinding


@dataclass(frozen=True)
class TagSmokePlan:
    source_root: Path
    repo_dir: Path
    install_dir: Path
    tag: str
    install_spec: str
    findings: tuple[PublishFinding, ...] = ()


def _is_empty_dir(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def _git_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def build_tag_smoke_plan(source_root: Path, repo_dir: Path, install_dir: Path, tag: str) -> TagSmokePlan:
    source = source_root.resolve()
    repo = repo_dir.resolve()
    install = install_dir.resolve()
    findings: list[PublishFinding] = []

    bundle_plan = build_bundle_plan(source, repo)
    findings.extend(bundle_plan.findings)
    if repo.exists() and not _is_empty_dir(repo):
        findings.append(PublishFinding(repo.as_posix(), "repo-dir-not-empty", "refusing to overwrite git smoke repo"))
    if install.exists() and not _is_empty_dir(install):
        findings.append(
            PublishFinding(install.as_posix(), "install-dir-not-empty", "refusing to overwrite git smoke install target")
        )

    return TagSmokePlan(
        source_root=source,
        repo_dir=repo,
        install_dir=install,
        tag=tag,
        install_spec=f"git+{_git_file_uri(repo)}@{tag}",
        findings=tuple(findings),
    )


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def _print_result(label: str, result: subprocess.CompletedProcess[str]) -> None:
    print(f"[{label}] rc={result.returncode}")
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())


def render(plan: TagSmokePlan) -> str:
    lines = [
        "# Ralph Local Tag Smoke",
        "",
        f"source={plan.source_root}",
        f"repo_dir={plan.repo_dir}",
        f"install_dir={plan.install_dir}",
        f"tag={plan.tag}",
        f"install_spec={plan.install_spec}",
        f"findings={len(plan.findings)}",
    ]
    if plan.findings:
        lines.extend(["", "## Findings", ""])
        for finding in plan.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def apply_tag_smoke(plan: TagSmokePlan) -> int:
    if plan.findings:
        print(render(plan))
        print("smoke=blocked")
        return 1

    from .publish_bundle import apply_bundle

    bundle_plan = build_bundle_plan(plan.source_root, plan.repo_dir)
    bundle_rc = apply_bundle(bundle_plan)
    if bundle_rc != 0:
        return bundle_rc

    commands = [
        ("git-init", ["git", "init"]),
        ("git-add", ["git", "add", "."]),
        (
            "git-commit",
            [
                "git",
                "-c",
                "user.name=Ralph Smoke",
                "-c",
                "user.email=ralph-smoke@example.invalid",
                "commit",
                "-m",
                f"release {plan.tag}",
            ],
        ),
        ("git-tag", ["git", "tag", plan.tag]),
    ]
    for label, cmd in commands:
        result = _run(cmd, cwd=plan.repo_dir)
        _print_result(label, result)
        if result.returncode != 0:
            print("smoke=failed")
            return result.returncode

    pip_cmd = [
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
    ]
    pip_result = _run(pip_cmd)
    _print_result("pip-install", pip_result)
    if pip_result.returncode != 0:
        print("smoke=failed")
        return pip_result.returncode

    env = {**os.environ, "PYTHONPATH": str(plan.install_dir)}
    import_result = _run(
        [
            sys.executable,
            "-c",
            (
                "from ralph_automation.sync import default_template_root; "
                "p=default_template_root(); "
                "print((p/'scripts'/'agent_worker.py').exists())"
            ),
        ],
        env=env,
    )
    _print_result("import-check", import_result)
    if import_result.returncode != 0 or "True" not in import_result.stdout:
        print("smoke=failed")
        return import_result.returncode or 1

    print(render(plan))
    print("smoke=passed")
    return 0


def run_tag_smoke(source_root: Path, repo_dir: Path, install_dir: Path, tag: str, *, check: bool, apply: bool) -> int:
    plan = build_tag_smoke_plan(source_root, repo_dir, install_dir, tag)
    if check:
        print(render(plan))
        return 1 if plan.findings else 0
    if apply:
        return apply_tag_smoke(plan)
    raise ValueError("either check or apply must be selected")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test installing Ralph automation from a local git tag")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Package source root")
    parser.add_argument("--repo-dir", type=Path, required=True, help="Temporary git repo directory")
    parser.add_argument("--install-dir", type=Path, required=True, help="Temporary pip target directory")
    parser.add_argument("--tag", default="v0.1.4", help="Local tag to create and install from")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report smoke plan without writing")
    mode.add_argument("--apply", action="store_true", help="Create local tag and install from it")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_tag_smoke(args.source, args.repo_dir, args.install_dir, args.tag, check=args.check, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
