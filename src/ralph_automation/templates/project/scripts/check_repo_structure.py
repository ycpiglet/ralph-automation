#!/usr/bin/env python3
"""Advisory repo-root structure report (TASK-246).

This script is read-only. It flags root-level runtime/generated/local/evidence
files that are easy for agents to mistake as source of truth. It does not
delete, move, or fail CI by default.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRS = {
    ".agents",
    ".githooks",
    ".github",
    "agents",
    "docs",
    "public",
    "schemas",
    "scripts",
    "src",
    "Managed database",
}

LOCAL_DIRS = {
    ".claude",
    ".codex",
    ".playwright-mcp",
    ".pytest_cache",
    "__pycache__",
    "schedule_runs",
}

ALLOWED_ROOT_FILES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    ".vercelignore",
    "AGENT_RUNTIME.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CURSOR.md",
    "Dockerfile",
    "EXAMPLES.md",
    "GEMINI.md",
    "README.md",
    "conftest.py",
    "deploy.sh",
    "docker-compose.yml",
    "nginx.conf",
    "package-lock.json",
    "package.json",
    "pytest.ini",
    "requirements.txt",
    "vercel.json",
}

SECRET_OR_LOCAL_FILES = {
    ".env",
}

GENERATED_ROOT_FILES = {
    "tasks.index.json",
    "tasks.events.jsonl",
    "eval_log.jsonl",
}

RUNTIME_PREFIXES = (
    ".tmp-http-",
)

EVIDENCE_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".txt",
)


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    recommendation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "category": self.category,
            "recommendation": self.recommendation,
        }


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_runtime_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in RUNTIME_PREFIXES)


def analyze(root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        name = path.name
        if name == ".git":
            continue
        rel = _rel(path, root)

        if path.is_dir():
            if name in SOURCE_DIRS:
                continue
            if name in LOCAL_DIRS:
                findings.append(
                    Finding(
                        rel,
                        "local-runtime-dir",
                        "Keep out of canonical decisions unless the directory is intentionally promoted to tracked repo config.",
                    )
                )
            else:
                findings.append(
                    Finding(
                        rel,
                        "unexpected-root-dir",
                        "Move source to an existing domain directory or document this directory in agents/OPERATING-STRUCTURE.md.",
                    )
                )
            continue

        if name in ALLOWED_ROOT_FILES:
            continue
        if name in SECRET_OR_LOCAL_FILES:
            findings.append(Finding(rel, "secret-or-local", "Never commit; keep ignored/local."))
        elif name in GENERATED_ROOT_FILES or _is_runtime_name(name):
            findings.append(
                Finding(
                    rel,
                    "generated-runtime-file",
                    "Keep ignored/local or move the generator output under an explicit runtime/generated directory in a later migration.",
                )
            )
        elif path.suffix.lower() in EVIDENCE_SUFFIXES:
            findings.append(
                Finding(
                    rel,
                    "root-evidence-file",
                    "Prefer role-local evidence folders such as agents/qa/, agents/beta_tester/, or agents/runtime/evidence/.",
                )
            )
        else:
            findings.append(
                Finding(
                    rel,
                    "uncategorized-root-file",
                    "Either add to the root contract or move to the owning domain directory.",
                )
            )
    return findings


def render(findings: list[Finding]) -> str:
    lines = [
        "# Repo Root Structure Report",
        "",
        "This is advisory. Source of truth remains the files themselves and the TASK records.",
        "",
        "| Path | Category | Recommendation |",
        "|------|----------|----------------|",
    ]
    if not findings:
        lines.append("| - | ok | Root matches the current contract. |")
    else:
        for finding in findings:
            lines.append(f"| `{finding.path}` | {finding.category} | {finding.recommendation} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory repo-root structure report")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable findings")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repo root override for tests")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    findings = analyze(args.root)
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        print(render(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
