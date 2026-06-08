#!/usr/bin/env python3
"""Thin host wrapper for the staged ralph_automation inventory module."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = ROOT / "packages" / "ralph-automation" / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from ralph_automation.inventory import (  # noqa: E402
    InventoryItem,
    analyze as _analyze,
    classify_path,
    render,
    run_inventory,
    unsafe_export_items,
)


def analyze(root: Path = ROOT) -> list[InventoryItem]:
    return _analyze(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify files for Ralph automation migration")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repo root override for tests")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable inventory")
    parser.add_argument("--check", action="store_true", help="Fail if unsafe paths are export candidates")
    parser.add_argument("--limit", type=int, default=20, help="Text report export-candidate sample size")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run_inventory(args.root, json_output=args.json, check=args.check, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
