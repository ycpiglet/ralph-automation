from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_PATH_PREFIXES = (
    ".agents/",
    ".claude/",
    ".codex/",
    ".playwright-mcp/",
    "agents/lead_engineer/tasks/",
    "agents/lead_engineer/reports/",
    "agents/lead_engineer/reviews/",
    "agents/lead_engineer/meetings/",
    "agents/messages/",
    "agents/runtime/",
    "docs/manuals/",
    "docs/vendor_docs/",
    "public/",
    "schedule_runs/",
    "src/assets/",
    "supabase/",
)

FORBIDDEN_PATH_NAMES = {
    ".env",
    ".env.local",
    "tasks.events.jsonl",
    "tasks.index.json",
    "ralph.yml",
}

BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".webp",
    ".zip",
}

SKIP_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    ".tmp",
    "__pycache__",
    "build",
    "dist",
}

_ABSOLUTE_PATH_RE = r"(?:[A-Za-z]:\\Users\\" + r"|/Us" + r"ers/|/ho" + r"me/)[^\s`\"']+"

CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("absolute-local-path", re.compile(_ABSOLUTE_PATH_RE)),
    ("secret-like-content", re.compile(r"(?i)\b(?:OPENAI|ANTHROPIC|SUPABASE|VERCEL)_[A-Z0-9_]*KEY\s*=")),
    ("secret-like-content", re.compile(r"\bsk-[A-Za-z0-9_-]{4,}")),
    ("secret-like-content", re.compile(r"(?i)\bservice[_-]?role\b")),
)

PROJECT_TEMPLATE_PREFIX = "src/ralph_automation/templates/project/"

HOST_HISTORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("concrete-task-id", re.compile(r"\b(?:TASK|CYCLE|REVIEW)-\d{3,}\b")),
    ("concrete-audit-id", re.compile(r"\bAUDIT-\d{4}-\d{2}-\d{2}-\d{3}\b")),
    ("concrete-meeting-id", re.compile(r"\bMEETING-\d{4}-\d{2}-\d{2}-\d{3}\b")),
    ("host-specific-project-history", re.compile(r"\b(?:TASK-250-ralph-automation-github-sync|CYCLE-091|2026-06-07-ralph-automation-github-sync)\b")),
    ("host-specific-account-reference", re.compile(r"\b(?:ANTHROPIC_API_KEY_KETI|_KETI)\b")),
    ("product-specific-reference", re.compile(r"\b(?:TAG\s+Manual|tag[_-]?manual|Supabase|RLS)\b|public/index\.html|supabase/")),
)

PROJECT_TEMPLATE_ID_SUFFIXES = {".md", ".yml", ".yaml", ".toml"}


@dataclass(frozen=True)
class SanitizationFinding:
    path: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _forbidden_path_kind(rel: str) -> str | None:
    if rel in FORBIDDEN_PATH_NAMES:
        return "forbidden-path"
    if any(rel.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
        return "forbidden-path"
    template_prefix = "src/ralph_automation/templates/project/"
    if rel.startswith(template_prefix):
        template_rel = rel.removeprefix(template_prefix)
        if template_rel in FORBIDDEN_PATH_NAMES or any(template_rel.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
            return "forbidden-template-path"
    return None


def _iter_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.relative_to(root).parts)
    )


def scan_public_content(path: Path, rel: str) -> list[SanitizationFinding]:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [SanitizationFinding(rel, "binary-or-undecodable", "public core should avoid undecodable files")]

    findings: list[SanitizationFinding] = []
    for kind, pattern in CONTENT_PATTERNS:
        if pattern.search(text):
            findings.append(SanitizationFinding(rel, kind, pattern.pattern))
    if rel.startswith(PROJECT_TEMPLATE_PREFIX):
        for detail, pattern in HOST_HISTORY_PATTERNS:
            if detail.startswith("concrete-") and path.suffix.lower() not in PROJECT_TEMPLATE_ID_SUFFIXES:
                continue
            if pattern.search(text):
                findings.append(SanitizationFinding(rel, "host-history-reference", detail))
    return findings


def analyze(root: Path) -> list[SanitizationFinding]:
    findings: list[SanitizationFinding] = []
    for path in _iter_files(root):
        rel = _rel(path, root)
        forbidden_kind = _forbidden_path_kind(rel)
        if forbidden_kind:
            findings.append(SanitizationFinding(rel, forbidden_kind, "host/product/local path must not be published"))
            continue
        findings.extend(scan_public_content(path, rel))
    return findings


def render(findings: list[SanitizationFinding]) -> str:
    lines = [
        "# Ralph Public Sanitization",
        "",
        f"findings={len(findings)}",
        "",
        "| Path | Kind | Detail |",
        "|------|------|--------|",
    ]
    if not findings:
        lines.append("| - | ok | No forbidden public package content found. |")
    else:
        for finding in findings:
            lines.append(f"| `{finding.path}` | {finding.kind} | {finding.detail} |")
    return "\n".join(lines)


def run_sanitize(root: Path, *, check: bool) -> int:
    findings = analyze(root)
    print(render(findings))
    if check and findings:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitize a staged Ralph public package")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Package root")
    parser.add_argument("--check", action="store_true", help="Fail if public sanitization findings exist")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run_sanitize(args.root, check=args.check)
