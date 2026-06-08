from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import RalphConfig, load_config
from .publish_check import PublishFinding
from .sync import _content_digest
from .sync import _template_files
from .sync import default_template_root


@dataclass(frozen=True)
class RalphLockPlan:
    root: Path
    config: RalphConfig
    lock_path: Path
    template_root: Path
    record: dict[str, Any]
    findings: tuple[PublishFinding, ...] = ()


def _template_digest(template_root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = _template_files(template_root)
    for path in files:
        rel = path.relative_to(template_root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_content(path))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}", len(files)


def _managed_files(template_root: Path) -> dict[str, str]:
    return {
        path.relative_to(template_root).as_posix(): _content_digest(path)
        for path in _template_files(template_root)
    }


def _canonical_content(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def build_lock_record(root: Path, template_root: Path | None = None) -> dict[str, Any]:
    config = load_config(root)
    resolved_template_root = template_root or default_template_root()
    digest, file_count = _template_digest(resolved_template_root)
    return {
        "schema": "ralph-lock/v1",
        "project": config.project,
        "upstream": {
            "package": config.upstream_package,
            "remote_url": config.upstream_remote_url,
            "ref": config.upstream_ref,
        },
        "installed": {
            "package_version": __version__,
            "template_digest": digest,
            "template_files": file_count,
            "managed_files": _managed_files(resolved_template_root),
        },
    }


def _load_existing_lock(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_lock_plan(root: Path, template_root: Path | None = None) -> RalphLockPlan:
    resolved_root = root.resolve()
    resolved_template_root = (template_root or default_template_root()).resolve()
    config = load_config(resolved_root)
    lock_path = resolved_root / "ralph.lock.json"
    record = build_lock_record(resolved_root, template_root=resolved_template_root)
    findings: list[PublishFinding] = []

    if not config.upstream_remote_url:
        findings.append(PublishFinding("ralph.yml", "missing-upstream-remote-url", "upstream.remote_url is required"))
    if not config.upstream_ref:
        findings.append(PublishFinding("ralph.yml", "missing-upstream-ref", "upstream.ref is required"))

    try:
        existing = _load_existing_lock(lock_path)
    except json.JSONDecodeError as exc:
        findings.append(PublishFinding("ralph.lock.json", "malformed-lock-file", str(exc)))
        existing = None

    if existing is None:
        findings.append(PublishFinding("ralph.lock.json", "missing-lock-file", "run ralph lock --write"))
    elif existing != record:
        findings.append(PublishFinding("ralph.lock.json", "lock-out-of-date", "run ralph lock --write"))

    return RalphLockPlan(
        root=resolved_root,
        config=config,
        lock_path=lock_path,
        template_root=resolved_template_root,
        record=record,
        findings=tuple(findings),
    )


def write_lock(plan: RalphLockPlan) -> None:
    plan.lock_path.write_text(json.dumps(plan.record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render(plan: RalphLockPlan) -> str:
    installed = plan.record["installed"]
    upstream = plan.record["upstream"]
    lines = [
        "# Ralph Lock",
        "",
        f"project={plan.config.project}",
        f"lock_path={plan.lock_path}",
        f"upstream_package={upstream['package']}",
        f"upstream_remote_url={upstream['remote_url']}",
        f"upstream_ref={upstream['ref']}",
        f"package_version={installed['package_version']}",
        f"template_digest={installed['template_digest']}",
        f"template_files={installed['template_files']}",
        f"findings={len(plan.findings)}",
    ]
    if plan.findings:
        lines.extend(["", "## Findings", ""])
        for finding in plan.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def _write_blockers(findings: tuple[PublishFinding, ...]) -> tuple[PublishFinding, ...]:
    return tuple(
        finding
        for finding in findings
        if finding.kind in {"missing-upstream-remote-url", "missing-upstream-ref"}
    )


def run_lock(root: Path, *, mode: str, template_root: Path | None = None) -> int:
    plan = build_lock_plan(root, template_root=template_root)
    if mode == "write":
        blockers = _write_blockers(plan.findings)
        if blockers:
            print(render(plan))
            return 1
        write_lock(plan)
        plan = build_lock_plan(root, template_root=template_root)
        print(render(plan))
        return 1 if plan.findings else 0
    if mode == "check":
        print(render(plan))
        return 1 if plan.findings else 0
    raise ValueError(f"unknown lock mode: {mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check or write the host Ralph upstream lock")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Host project root")
    parser.add_argument("--template-root", type=Path, default=None, help="Template root override")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if ralph.lock.json is missing or stale")
    mode.add_argument("--write", action="store_true", help="Write ralph.lock.json for the current installed package")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_lock(args.root, mode="write" if args.write else "check", template_root=args.template_root)


if __name__ == "__main__":
    raise SystemExit(main())
