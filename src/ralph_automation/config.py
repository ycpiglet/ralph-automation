from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RalphConfig:
    project: str
    sync_mode: str
    allow_silent_overwrite: bool
    path: Path
    upstream_package: str = ""
    upstream_remote_url: str = ""
    upstream_ref: str = ""


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def load_config(root: Path) -> RalphConfig:
    path = root / "ralph.yml"
    if not path.exists():
        raise FileNotFoundError(f"ralph.yml not found under {root}")

    project = ""
    sync_mode = ""
    allow_silent_overwrite: bool | None = None
    upstream_package = ""
    upstream_remote_url = ""
    upstream_ref = ""
    section = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if indent == 0:
            section = key if not value else ""
            if key == "project":
                project = value
            continue

        if section == "sync" and key == "mode":
            sync_mode = value
        elif section == "sync" and key == "allow_silent_overwrite":
            allow_silent_overwrite = _parse_bool(value)
        elif section == "upstream" and key == "package":
            upstream_package = value
        elif section == "upstream" and key == "remote_url":
            upstream_remote_url = value
        elif section == "upstream" and key == "ref":
            upstream_ref = value

    if not project:
        raise ValueError(f"{path} is missing project")
    if not sync_mode:
        raise ValueError(f"{path} is missing sync.mode")
    if allow_silent_overwrite is None:
        raise ValueError(f"{path} is missing sync.allow_silent_overwrite")

    return RalphConfig(
        project=project,
        sync_mode=sync_mode,
        allow_silent_overwrite=allow_silent_overwrite,
        path=path,
        upstream_package=upstream_package,
        upstream_remote_url=upstream_remote_url,
        upstream_ref=upstream_ref,
    )
