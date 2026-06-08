from __future__ import annotations

import argparse
import difflib
from dataclasses import dataclass
from pathlib import Path

from .inventory import analyze as analyze_inventory
from .inventory import export_candidates
from .sanitize import SanitizationFinding, scan_public_content


@dataclass(frozen=True)
class ExportTemplate:
    path: str
    source: str
    source_path: Path
    target_path: Path


@dataclass(frozen=True)
class ExportPlan:
    host_root: Path
    package_root: Path
    template_root: Path
    creates: tuple[ExportTemplate, ...] = ()
    conflicts: tuple[ExportTemplate, ...] = ()
    unsafe: tuple[SanitizationFinding, ...] = ()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _default_package_root(host_root: Path) -> Path:
    return host_root / "packages" / "ralph-automation"


def _destination_for(package_root: Path, source_rel: str) -> Path:
    return package_root / "src" / "ralph_automation" / "templates" / "project" / Path(source_rel)


def build_export_plan(host_root: Path, package_root: Path | None = None) -> ExportPlan:
    resolved_host = host_root.resolve()
    resolved_package = (package_root or _default_package_root(resolved_host)).resolve()
    template_root = resolved_package / "src" / "ralph_automation" / "templates" / "project"
    creates: list[ExportTemplate] = []
    conflicts: list[ExportTemplate] = []
    unsafe: list[SanitizationFinding] = []

    for item in export_candidates(analyze_inventory(resolved_host)):
        source = resolved_host / item.path
        if _is_under(source, resolved_package):
            continue
        if not source.is_file():
            continue

        target = _destination_for(resolved_package, item.path)
        public_rel = target.relative_to(resolved_package).as_posix()
        unsafe.extend(scan_public_content(source, public_rel))
        template = ExportTemplate(
            path=target.relative_to(template_root).as_posix(),
            source=item.path,
            source_path=source,
            target_path=target,
        )
        if not target.exists():
            creates.append(template)
            continue
        if _read_bytes(source) != _read_bytes(target):
            conflicts.append(template)

    return ExportPlan(
        host_root=resolved_host,
        package_root=resolved_package,
        template_root=template_root,
        creates=tuple(creates),
        conflicts=tuple(conflicts),
        unsafe=tuple(unsafe),
    )


def render_check(plan: ExportPlan) -> str:
    status = "blocked" if plan.unsafe or plan.conflicts else "ready"
    lines = [
        "# Ralph Public Export",
        "",
        f"host_root={plan.host_root}",
        f"package_root={plan.package_root}",
        f"template_root={plan.template_root}",
        f"status={status}",
        f"creates={len(plan.creates)}",
        f"conflicts={len(plan.conflicts)}",
        f"unsafe={len(plan.unsafe)}",
    ]
    for item in plan.creates:
        lines.append(f"- create {item.path} <- {item.source}")
    for item in plan.conflicts:
        lines.append(f"- conflict {item.path} <- {item.source}")
    for finding in plan.unsafe:
        lines.append(f"- unsafe {finding.path}: {finding.kind}")
    return "\n".join(lines)


def _read_text_best_effort(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return [f"<binary {path.name}>\n"]


def _diff_template(template: ExportTemplate) -> str:
    source_lines = _read_text_best_effort(template.source_path)
    if template.target_path.exists():
        target_lines = _read_text_best_effort(template.target_path)
        fromfile = f"package/{template.path}"
    else:
        target_lines = []
        fromfile = "/dev/null"
    return "".join(
        difflib.unified_diff(
            target_lines,
            source_lines,
            fromfile=fromfile,
            tofile=f"host/{template.source}",
        )
    ).rstrip()


def render_diff(plan: ExportPlan) -> str:
    all_items = [*plan.creates, *plan.conflicts]
    if not all_items:
        return "No public export template changes available."
    return "\n\n".join(_diff_template(item) for item in all_items)


def apply_export(plan: ExportPlan) -> int:
    if plan.unsafe or plan.conflicts:
        print(render_check(plan))
        print("applied=0")
        return 1

    applied = 0
    for item in plan.creates:
        item.target_path.parent.mkdir(parents=True, exist_ok=True)
        item.target_path.write_bytes(_read_bytes(item.source_path))
        applied += 1

    if not plan.creates:
        print("No public export template changes available.")
    else:
        print(render_check(plan))
    print(f"applied={applied}")
    return 0


def run_export(host_root: Path, mode: str, package_root: Path | None = None) -> int:
    plan = build_export_plan(host_root, package_root=package_root)
    if mode == "check":
        print(render_check(plan))
        return 1 if plan.unsafe or plan.conflicts else 0
    if mode == "diff":
        print(render_diff(plan))
        return 0
    if mode == "apply":
        return apply_export(plan)
    raise ValueError(f"unknown export mode: {mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage host automation candidates into the Ralph public package")
    parser.add_argument("--host-root", type=Path, default=Path.cwd(), help="Host project root")
    parser.add_argument("--package-root", type=Path, default=None, help="Ralph package root")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report export readiness without writing")
    mode.add_argument("--diff", action="store_true", help="Show exact template staging changes")
    mode.add_argument("--apply", action="store_true", help="Copy missing safe templates into the package")
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
    return run_export(args.host_root, mode, package_root=args.package_root)
