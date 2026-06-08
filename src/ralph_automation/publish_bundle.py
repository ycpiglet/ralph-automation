from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from .publish_check import PublishFinding, analyze as analyze_publish
from .sanitize import analyze as analyze_sanitize


INCLUDE_FILES = (
    ".gitignore",
    "pyproject.toml",
    "README.md",
)

INCLUDE_DIRS = (
    ".github",
    "src",
    "tests",
)

SKIP_PARTS = {
    "__pycache__",
    ".pytest_cache",
    "build",
    "dist",
}

SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
}


@dataclass(frozen=True)
class BundleFile:
    path: str
    source: Path
    target: Path


@dataclass(frozen=True)
class BundlePlan:
    source_root: Path
    dest_root: Path
    files: tuple[BundleFile, ...]
    findings: tuple[PublishFinding, ...] = ()


def _is_empty_dir(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def _iter_dir_files(root: Path, rel_dir: str) -> list[Path]:
    base = root / rel_dir
    if not base.exists():
        return []
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_PARTS for part in rel_parts):
            continue
        if any(part.endswith(".egg-info") for part in rel_parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix().lower())


def _source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in INCLUDE_FILES:
        path = source_root / rel
        if path.exists() and path.is_file():
            files.append(path)
    for rel_dir in INCLUDE_DIRS:
        files.extend(_iter_dir_files(source_root, rel_dir))
    return sorted(files, key=lambda p: p.relative_to(source_root).as_posix().lower())


def build_bundle_plan(source_root: Path, dest_root: Path) -> BundlePlan:
    source = source_root.resolve()
    dest = dest_root.resolve()
    findings: list[PublishFinding] = []

    findings.extend(analyze_publish(source))
    sanitize_findings = analyze_sanitize(source)
    findings.extend(
        PublishFinding(finding.path, f"sanitize:{finding.kind}", finding.detail)
        for finding in sanitize_findings
    )
    if dest.exists() and not _is_empty_dir(dest):
        findings.append(PublishFinding(dest.as_posix(), "destination-not-empty", "refusing to overwrite bundle dest"))

    files = tuple(
        BundleFile(path.relative_to(source).as_posix(), path, dest / path.relative_to(source))
        for path in _source_files(source)
    )
    return BundlePlan(source, dest, files, tuple(findings))


def render(plan: BundlePlan) -> str:
    lines = [
        "# Ralph Publish Bundle",
        "",
        f"source={plan.source_root}",
        f"dest={plan.dest_root}",
        f"files={len(plan.files)}",
        f"findings={len(plan.findings)}",
        "",
        "| Path | Status |",
        "|------|--------|",
    ]
    if not plan.files:
        lines.append("| - | no files selected |")
    else:
        for item in plan.files[:50]:
            lines.append(f"| `{item.path}` | selected |")
        if len(plan.files) > 50:
            lines.append(f"| ... | {len(plan.files) - 50} more files omitted |")
    if plan.findings:
        lines.extend(["", "## Findings", ""])
        for finding in plan.findings:
            lines.append(f"- `{finding.path}` {finding.kind}: {finding.detail}")
    return "\n".join(lines)


def apply_bundle(plan: BundlePlan) -> int:
    if plan.findings:
        print(render(plan))
        print("applied=0")
        return 1
    for item in plan.files:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.target)
    print(render(plan))
    print(f"applied={len(plan.files)}")
    return 0


def run_publish_bundle(source_root: Path, dest_root: Path, *, check: bool, apply: bool) -> int:
    plan = build_bundle_plan(source_root, dest_root)
    if check:
        print(render(plan))
        return 1 if plan.findings else 0
    if apply:
        return apply_bundle(plan)
    raise ValueError("either check or apply must be selected")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a clean public GitHub source bundle for Ralph automation")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Package source root")
    parser.add_argument("--dest", type=Path, required=True, help="Destination directory for the clean source bundle")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report selected files and findings without writing")
    mode.add_argument("--apply", action="store_true", help="Copy clean public source files into dest")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_publish_bundle(args.source, args.dest, check=args.check, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
