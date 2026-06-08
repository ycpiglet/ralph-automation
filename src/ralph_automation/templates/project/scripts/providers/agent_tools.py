"""Tool execution + guardrails for the claude-agent provider (TASK-110).

ToolRunner executes the agent's tool calls against a single repo root with hard
guardrails:
  - filesystem access is confined to `root` (no escapes, no secret files)
  - run_command only runs whitelisted commands (no `git push`, no pip, no rm)
No anthropic dependency lives here so it is unit-testable on its own.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


class GuardrailError(Exception):
    """Raised when a tool call violates a safety guardrail."""


SECRET_NAMES = {".env"}
ALLOWED_CMDS = {"pytest", "python", "py"}
ALLOWED_GIT_SUBCMDS = {
    "status", "diff", "add", "commit", "checkout", "branch",
    "log", "restore", "rev-parse", "stash",
}
MAX_OUTPUT = 8000


def resolve_in_root(root: Path, path: str) -> Path:
    """Resolve `path` under `root`, rejecting escapes and secret files."""
    root = Path(root).resolve()
    p = (root / path).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise GuardrailError(f"path escapes repo root: {path}")
    if p.name in SECRET_NAMES:
        raise GuardrailError(f"access to secret file denied: {path}")
    return p


class ToolRunner:
    """Executes one tool call at a time against `root`, tracking changed files."""

    def __init__(self, root: Path, *, max_output: int = MAX_OUTPUT,
                 command_timeout: float = 120.0):
        self.root = Path(root).resolve()
        self.max_output = max_output
        self.command_timeout = command_timeout
        self.changed_files: list[str] = []

    def _track(self, path: str) -> None:
        if path not in self.changed_files:
            self.changed_files.append(path)

    def read_file(self, path: str) -> str:
        p = resolve_in_root(self.root, path)
        if not p.is_file():
            return f"ERROR: not a file: {path}"
        return p.read_text(encoding="utf-8", errors="replace")[: self.max_output]

    def list_dir(self, path: str = ".") -> str:
        p = resolve_in_root(self.root, path)
        if not p.is_dir():
            return f"ERROR: not a directory: {path}"
        return "\n".join(sorted(
            c.name + ("/" if c.is_dir() else "") for c in p.iterdir()
        ))

    def write_file(self, path: str, content: str) -> str:
        p = resolve_in_root(self.root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self._track(path)
        return f"OK: wrote {len(content)} chars to {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        p = resolve_in_root(self.root, path)
        if not p.is_file():
            return f"ERROR: not a file: {path}"
        text = p.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return f"ERROR: old string not found in {path}"
        if count > 1:
            return f"ERROR: old string not unique in {path} ({count} matches)"
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        self._track(path)
        return f"OK: edited {path}"

    def run_command(self, command: str) -> str:
        argv = shlex.split(command)
        if not argv:
            return "ERROR: empty command"
        if argv[0] not in ALLOWED_CMDS and argv[0] != "git":
            return f"ERROR: command not allowed: {argv[0]}"
        if argv[0] == "git":
            sub = argv[1] if len(argv) > 1 else ""
            if sub not in ALLOWED_GIT_SUBCMDS:
                return f"ERROR: git subcommand not allowed: {sub or '(none)'}"
            if sub == "add":
                add_args = argv[2:]
                whole_tree = {".", "-A", "--all", "-a", "-u", "--update", ":/"}
                if not add_args or any(a in whole_tree for a in add_args):
                    return ("ERROR: 'git add' must list explicit file paths "
                            "(no '.', '-A', '--all'). Stage only the files you changed.")
        if argv[0] in {"python", "py"} and "pip" in argv:
            return "ERROR: pip is not allowed"
        try:
            proc = subprocess.run(
                argv, cwd=str(self.root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {self.command_timeout}s"
        except FileNotFoundError as exc:
            return f"ERROR: command not found: {exc}"
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"[exit {proc.returncode}]\n{out[: self.max_output]}"

    def dispatch(self, name: str, args: dict) -> str:
        try:
            if name == "read_file":
                return self.read_file(args["path"])
            if name == "list_dir":
                return self.list_dir(args.get("path", "."))
            if name == "write_file":
                return self.write_file(args["path"], args["content"])
            if name == "edit_file":
                return self.edit_file(args["path"], args["old"], args["new"])
            if name == "run_command":
                return self.run_command(args["command"])
            return f"ERROR: unknown tool: {name}"
        except GuardrailError as exc:
            return f"GUARDRAIL: {exc}"
        except KeyError as exc:
            return f"ERROR: missing argument {exc}"


TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the repo. Returns file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "repo-relative path"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List entries of a directory inside the repo.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "repo-relative path (default '.')"}},
            "required": [],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file inside the repo with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one unique occurrence of `old` with `new` in a repo file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a whitelisted command in the repo root: pytest, python, or git "
            "(status/diff/add/commit/checkout/branch/log). git push and pip are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]
