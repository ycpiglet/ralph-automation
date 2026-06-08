from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import RalphConfig, load_config
from .publish_check import PublishFinding


@dataclass(frozen=True)
class HostUpdatePlan:
    root: Path
    config: RalphConfig
    install_dir: Path
    install_spec: str
    commands: tuple[str, ...]
    findings: tuple[PublishFinding, ...] = ()


@dataclass(frozen=True)
class HostUpdateStep:
    name: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class HostUpdateExecution:
    plan: HostUpdatePlan
    mode: str
    steps: tuple[HostUpdateStep, ...]
    findings: tuple[PublishFinding, ...] = ()


StepRunner = Callable[[HostUpdateStep], int]


def _install_spec(remote_url: str, ref: str) -> str:
    if remote_url.startswith("git@github.com:"):
        path = remote_url.removeprefix("git@github.com:")
        return f"git+ssh://git@github.com/{path}@{ref}"
    return f"git+{remote_url}@{ref}"


def _is_github_remote(remote_url: str) -> bool:
    return remote_url.startswith("https://github.com/") or remote_url.startswith("git@github.com:")


PLACEHOLDER_REMOTE_OWNERS = {"OWNER", "USER", "ORG", "ORGANIZATION", "<OWNER>", "<USER>", "<ORG>"}


def _github_remote_owner(remote_url: str) -> str | None:
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
    return parts[0]


def _is_placeholder_remote(remote_url: str) -> bool:
    owner = _github_remote_owner(remote_url)
    return bool(owner and owner.strip().upper() in PLACEHOLDER_REMOTE_OWNERS)


def _is_pinned_ref(ref: str) -> bool:
    if re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", ref):
        return True
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", ref))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _install_dir_findings(root: Path, install_dir: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    resolved_root = root.resolve()
    resolved_install = install_dir.resolve()
    allowed_roots = (resolved_root / ".tmp", resolved_root / ".ralph")
    if (
        resolved_install == resolved_root
        or not _is_relative_to(resolved_install, resolved_root)
        or not any(_is_relative_to(resolved_install, allowed) for allowed in allowed_roots)
    ):
        findings.append(
            PublishFinding(
                "install-dir",
                "unsafe-install-dir",
                "install dir must be under host .tmp/ or .ralph/",
            )
        )
    if resolved_install.exists() and any(resolved_install.iterdir()):
        findings.append(
            PublishFinding(
                "install-dir",
                "install-dir-not-empty",
                "install dir must be empty before executable update",
            )
        )
    return findings


def _execution_findings(plan: HostUpdatePlan) -> tuple[PublishFinding, ...]:
    return tuple(plan.findings)


def _trust_findings(config: RalphConfig) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    if config.upstream_package and config.upstream_package != "ralph-automation":
        findings.append(
            PublishFinding("ralph.yml", "unexpected-upstream-package", "upstream.package must be ralph-automation")
        )
    if config.upstream_remote_url and not _is_github_remote(config.upstream_remote_url):
        findings.append(
            PublishFinding("ralph.yml", "non-github-upstream-remote-url", "upstream.remote_url must be a GitHub URL")
        )
    if config.upstream_remote_url and _is_placeholder_remote(config.upstream_remote_url):
        findings.append(
            PublishFinding("ralph.yml", "placeholder-remote-url", "replace placeholder GitHub owner before release")
        )
    if config.upstream_ref and not _is_pinned_ref(config.upstream_ref):
        findings.append(
            PublishFinding("ralph.yml", "mutable-upstream-ref", "upstream.ref must be a release tag or 40-char commit SHA")
        )
    return findings


def _python_literal(path: Path) -> str:
    return repr(str(path))


def _cli_command(install_dir: Path, args: list[str]) -> str:
    rendered_args = ", ".join(repr(arg) for arg in args)
    return (
        "python -c \"import sys; "
        f"sys.path.insert(0, {_python_literal(install_dir)}); "
        "from ralph_automation.cli import main; "
        f"raise SystemExit(main([{rendered_args}]))\""
    )


def _step_name(args: list[str]) -> str:
    if args[:1] == ["sync"]:
        if "--check" in args:
            return "sync-check"
        if "--diff" in args:
            return "sync-diff"
        if "--apply" in args:
            return "sync-apply"
    if args[:1] == ["lock"] and "--write" in args:
        return "lock-write"
    return "-".join(arg.lstrip("-") for arg in args if not arg.startswith(str(Path(args[-1]).anchor)))


def _cli_step(install_dir: Path, args: list[str]) -> HostUpdateStep:
    rendered_args = ", ".join(repr(arg) for arg in args)
    code = (
        "import sys; "
        f"sys.path.insert(0, {_python_literal(install_dir)}); "
        "from ralph_automation.cli import main; "
        f"raise SystemExit(main([{rendered_args}]))"
    )
    return HostUpdateStep(name=_step_name(args), args=(sys.executable, "-c", code))


def _template_check_step(install_dir: Path) -> HostUpdateStep:
    code = (
        "import sys; "
        f"sys.path.insert(0, {_python_literal(install_dir)}); "
        "from ralph_automation.sync import default_template_root; "
        "p=default_template_root(); "
        "sentinel=(p/'scripts'/'agent_worker.py').exists(); "
        "print(f'template_sentinel={sentinel}'); "
        "raise SystemExit(0 if sentinel else 1)"
    )
    return HostUpdateStep("verify-templates", (sys.executable, "-c", code))


def build_update_plan(root: Path, install_dir: Path) -> HostUpdatePlan:
    resolved_root = root.resolve()
    resolved_install = install_dir.resolve()
    config = load_config(resolved_root)
    findings: list[PublishFinding] = []

    if not config.upstream_remote_url:
        findings.append(PublishFinding("ralph.yml", "missing-upstream-remote-url", "upstream.remote_url is required"))
    if not config.upstream_ref:
        findings.append(PublishFinding("ralph.yml", "missing-upstream-ref", "upstream.ref is required"))
    findings.extend(_trust_findings(config))
    findings.extend(_install_dir_findings(resolved_root, resolved_install))

    install_spec = _install_spec(config.upstream_remote_url, config.upstream_ref) if not findings else ""
    commands = (
        f"python -m pip install --target {resolved_install} {install_spec} --upgrade --no-deps --no-build-isolation --no-cache-dir",
        (
            "python -c \"import sys; "
            f"sys.path.insert(0, {_python_literal(resolved_install)}); "
            "from ralph_automation.sync import default_template_root; "
            "p=default_template_root(); sentinel=(p/'scripts'/'agent_worker.py').exists(); "
            "print(f'template_sentinel={sentinel}'); raise SystemExit(0 if sentinel else 1)\""
        ),
        _cli_command(resolved_install, ["sync", "--root", str(resolved_root), "--check"]),
        _cli_command(resolved_install, ["sync", "--root", str(resolved_root), "--diff"]),
        _cli_command(resolved_install, ["sync", "--root", str(resolved_root), "--apply"]),
        _cli_command(resolved_install, ["lock", "--root", str(resolved_root), "--write"]),
    )
    return HostUpdatePlan(resolved_root, config, resolved_install, install_spec, commands, tuple(findings))


def build_update_execution(root: Path, install_dir: Path, *, mode: str) -> HostUpdateExecution:
    if mode not in {"check", "diff", "apply"}:
        raise ValueError(f"unknown update mode: {mode}")
    plan = build_update_plan(root, install_dir)
    findings = _execution_findings(plan)
    steps: list[HostUpdateStep] = []
    if not findings:
        steps.append(
            HostUpdateStep(
                "install-upstream",
                (
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    str(plan.install_dir),
                    plan.install_spec,
                    "--upgrade",
                    "--no-deps",
                    "--no-build-isolation",
                    "--no-cache-dir",
                ),
            )
        )
        steps.append(_template_check_step(plan.install_dir))
        steps.append(_cli_step(plan.install_dir, ["sync", "--root", str(plan.root), "--check"]))
        if mode in {"diff", "apply"}:
            steps.append(_cli_step(plan.install_dir, ["sync", "--root", str(plan.root), "--diff"]))
        if mode == "apply":
            steps.append(_cli_step(plan.install_dir, ["sync", "--root", str(plan.root), "--apply"]))
            steps.append(_cli_step(plan.install_dir, ["sync", "--root", str(plan.root), "--check"]))
            steps.append(_cli_step(plan.install_dir, ["lock", "--root", str(plan.root), "--write"]))
    return HostUpdateExecution(plan=plan, mode=mode, steps=tuple(steps), findings=findings)


def render(plan: HostUpdatePlan) -> str:
    lines = [
        "# Ralph Host Update Plan",
        "",
        f"project={plan.config.project}",
        f"upstream_package={plan.config.upstream_package}",
        f"upstream_remote_url={plan.config.upstream_remote_url}",
        f"upstream_ref={plan.config.upstream_ref}",
        f"install_dir={plan.install_dir}",
        f"install_spec={plan.install_spec}",
        f"findings={len(plan.findings)}",
        "",
        "## Commands",
        "",
    ]
    for command in plan.commands:
        lines.append(f"- `{command}`")
    if plan.findings:
        lines.extend(["", "## Findings", ""])
        for finding in plan.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def run_update_plan(root: Path, install_dir: Path, *, check: bool) -> int:
    plan = build_update_plan(root, install_dir)
    print(render(plan))
    return 1 if check and plan.findings else 0


def _default_runner(step: HostUpdateStep) -> int:
    return subprocess.run(step.args, check=False).returncode


def _format_args(args: tuple[str, ...]) -> str:
    return " ".join(args)


def run_update(root: Path, install_dir: Path, *, mode: str, runner: StepRunner | None = None) -> int:
    execution = build_update_execution(root, install_dir, mode=mode)
    print(render(execution.plan))
    if execution.findings:
        extra_findings = [finding for finding in execution.findings if finding not in execution.plan.findings]
        if extra_findings:
            print("")
            print("## Execution Findings")
            print("")
            for finding in extra_findings:
                print(f"- `{finding.path}` {finding.kind}: {finding.detail}")
        return 1
    print("")
    print("# Ralph Host Update")
    print("")
    print(f"mode={execution.mode}")
    print(f"steps={len(execution.steps)}")
    active_runner = runner or _default_runner
    for step in execution.steps:
        print(f"$ {_format_args(step.args)}")
        code = active_runner(step)
        if code != 0:
            print(f"failed_step={step.name}")
            print(f"exit_code={code}")
            return code
    print("update=complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or run host update from the configured Ralph upstream")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Host project root")
    parser.add_argument("--install-dir", type=Path, required=True, help="Temporary install target for upstream package")
    parser.add_argument("--check", action="store_true", help="Fail if host upstream config is incomplete")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_update_plan(args.root, args.install_dir, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
