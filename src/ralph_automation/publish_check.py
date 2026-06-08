from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublishFinding:
    path: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _ignored_by_package_gitignore(root: Path, pattern: str) -> bool:
    text = _read(root / ".gitignore")
    return any(line.strip() == pattern for line in text.splitlines())


def analyze(root: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []

    required_files = (
        "pyproject.toml",
        "README.md",
        ".github/workflows/test.yml",
        "src/ralph_automation/__init__.py",
    )
    for rel in required_files:
        if not (root / rel).exists():
            findings.append(PublishFinding(rel, "missing-file", "required for public GitHub source"))

    pyproject = _read(root / "pyproject.toml")
    if "templates/project/**/*" not in pyproject:
        findings.append(
            PublishFinding("pyproject.toml", "missing-package-data", "templates must ship with GitHub tag installs")
        )

    workflow = _read(root / ".github" / "workflows" / "test.yml")
    if "pytest tests -q" not in workflow:
        findings.append(PublishFinding(".github/workflows/test.yml", "missing-ci-test", "package tests must run in CI"))
    if "sanitize --root . --check" not in workflow:
        findings.append(
            PublishFinding(".github/workflows/test.yml", "missing-ci-sanitize", "public source must run sanitize gate")
        )

    package_template = root / "src" / "ralph_automation" / "templates" / "project"
    if not (package_template / "scripts" / "agent_worker.py").exists():
        findings.append(
            PublishFinding(
                "src/ralph_automation/templates/project",
                "missing-package-template",
                "sync templates must be package data",
            )
        )

    duplicate_templates = root / "templates"
    if duplicate_templates.exists() and not _ignored_by_package_gitignore(root, "/templates/"):
        findings.append(
            PublishFinding(
                "templates",
                "duplicate-template-tree",
                "top-level templates are staging leftovers; package-data templates are canonical",
            )
        )

    return findings


def render(findings: list[PublishFinding]) -> str:
    lines = [
        "# Ralph Publish Check",
        "",
        f"findings={len(findings)}",
        "",
        "| Path | Kind | Detail |",
        "|------|------|--------|",
    ]
    if not findings:
        lines.append("| - | ok | Public GitHub source contract is ready. |")
    else:
        for finding in findings:
            lines.append(f"| `{finding.path}` | {finding.kind} | {finding.detail} |")
    return "\n".join(lines)


def run_publish_check(root: Path, *, check: bool) -> int:
    findings = analyze(root)
    print(render(findings))
    if check and findings:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Ralph package readiness for public GitHub publication")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Package root")
    parser.add_argument("--check", action="store_true", help="Fail if publish-readiness findings exist")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_publish_check(args.root, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
