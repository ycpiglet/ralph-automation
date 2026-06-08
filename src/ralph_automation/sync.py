from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import RalphConfig, load_config


@dataclass(frozen=True)
class TemplateUpdate:
    path: str
    action: str
    source: Path
    target: Path


@dataclass(frozen=True)
class SyncPlan:
    root: Path
    config: RalphConfig
    template_root: Path
    updates: tuple[TemplateUpdate, ...] = ()
    conflicts: tuple[TemplateUpdate, ...] = ()


def default_template_root() -> Path:
    return Path(__file__).resolve().parent / "templates" / "project"


def _is_runtime_artifact(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def _template_files(template_root: Path) -> list[Path]:
    if not template_root.exists():
        return []
    return sorted(
        (path for path in template_root.rglob("*") if path.is_file() and not _is_runtime_artifact(path)),
        key=lambda path: path.relative_to(template_root).as_posix().lower(),
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _canonical_content(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _content_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(_canonical_content(path)).hexdigest()}"


def _load_managed_files(root: Path) -> dict[str, str]:
    lock_path = root / "ralph.lock.json"
    if not lock_path.exists():
        return {}
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    managed = data.get("installed", {}).get("managed_files", {})
    if not isinstance(managed, dict):
        return {}
    return {str(path): str(digest) for path, digest in managed.items()}


def build_sync_plan(root: Path, template_root: Path | None = None) -> SyncPlan:
    config = load_config(root)
    resolved_template_root = template_root or default_template_root()
    managed_files = _load_managed_files(root)
    updates: list[TemplateUpdate] = []
    conflicts: list[TemplateUpdate] = []
    for source in _template_files(resolved_template_root):
        rel = source.relative_to(resolved_template_root).as_posix()
        target = root / rel
        update = TemplateUpdate(rel, "create", source, target)
        if not target.exists():
            updates.append(update)
            continue
        if _read(source) == _read(target):
            continue
        if managed_files.get(rel) == _content_digest(target):
            updates.append(TemplateUpdate(rel, "update", source, target))
            continue
        conflicts.append(TemplateUpdate(rel, "conflict", source, target))
    return SyncPlan(
        root=root,
        config=config,
        template_root=resolved_template_root,
        updates=tuple(updates),
        conflicts=tuple(conflicts),
    )


def render_check(plan: SyncPlan) -> str:
    status = "blocked" if plan.config.allow_silent_overwrite else "ready"
    lines = [
        "# Ralph Sync Check",
        "",
        f"project={plan.config.project}",
        f"mode={plan.config.sync_mode}",
        f"allow_silent_overwrite={str(plan.config.allow_silent_overwrite).lower()}",
        f"status={status}",
        f"updates={len(plan.updates)}",
        f"conflicts={len(plan.conflicts)}",
    ]
    for update in plan.updates:
        lines.append(f"- {update.action} {update.path}")
    for conflict in plan.conflicts:
        lines.append(f"- conflict {conflict.path}")
    return "\n".join(lines)


def _diff_update(update: TemplateUpdate) -> str:
    source_lines = _read(update.source).splitlines(keepends=True)
    if update.target.exists():
        target_lines = _read(update.target).splitlines(keepends=True)
        fromfile = f"host/{update.path}"
    else:
        target_lines = []
        fromfile = "/dev/null"
    return "".join(
        difflib.unified_diff(
            target_lines,
            source_lines,
            fromfile=fromfile,
            tofile=f"upstream/{update.path}",
        )
    ).rstrip()


def render_diff(plan: SyncPlan) -> str:
    all_items = [*plan.updates, *plan.conflicts]
    if not all_items:
        return "No template updates available."
    return "\n\n".join(_diff_update(update) for update in all_items)


def apply_updates(plan: SyncPlan) -> int:
    if plan.conflicts:
        print(render_check(plan))
        print("applied=0")
        return 1
    applied = 0
    for update in plan.updates:
        update.target.parent.mkdir(parents=True, exist_ok=True)
        update.target.write_text(_read(update.source), encoding="utf-8")
        applied += 1
    if not plan.updates:
        print("No template updates available.")
    else:
        print(render_check(plan))
    print(f"applied={applied}")
    return 0


def run_sync(root: Path, mode: str, template_root: Path | None = None) -> int:
    plan = build_sync_plan(root, template_root=template_root)
    if plan.config.allow_silent_overwrite:
        print(render_check(plan))
        print("ERROR: sync.allow_silent_overwrite must be false.")
        return 1

    if mode == "check":
        print(render_check(plan))
        return 1 if plan.conflicts else 0
    elif mode == "diff":
        print(render_diff(plan))
    elif mode == "apply":
        return apply_updates(plan)
    else:
        raise ValueError(f"unknown sync mode: {mode}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check/diff/apply Ralph template updates")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Host project root")
    parser.add_argument("--template-root", type=Path, default=None, help="Template root override")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report available updates without writing")
    mode.add_argument("--diff", action="store_true", help="Show exact template changes")
    mode.add_argument("--apply", action="store_true", help="Apply safe selected updates")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check:
        mode = "check"
    elif args.diff:
        mode = "diff"
    else:
        mode = "apply"
    return run_sync(args.root, mode, template_root=args.template_root)
