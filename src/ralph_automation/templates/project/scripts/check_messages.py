#!/usr/bin/env python3
"""
Lint for agents/messages/ — TASK-083.

Catches structural breakage:
  - missing or malformed frontmatter
  - required field missing
  - id mismatched with filename
  - ts not parseable as ISO 8601
  - status / type outside enum
  - reply without in_reply_to
  - in_reply_to references unknown id
  - duplicate ids
  - duplicate claimed on same parent
  - stale open (>24h) — WARN
  - archived without prior answered/blocked — WARN
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
MESSAGES_DIR = REPO_ROOT / "agents" / "messages"
SCAN_DIRS = ["inbox", "archive", "samples"]

REQUIRED_FIELDS = ["id", "from", "to", "task_id", "intent", "type", "status", "ts"]
TYPE_ENUM = {
    "request",
    "reply",
    "heartbeat",
    "handoff",
    "escalation",
    "subagent_call",
    "subagent_reply",
    "question",
    "answer",
    "consensus",
    "retro_request",
    "retro_reply",
    "seminar_submission",
    "seminar_aggregate",
    "seminar_revision",
}
STATUS_ENUM = {"open", "claimed", "answered", "blocked", "archived"}

# TASK-119 — types that require an in_reply_to back-reference (extends the
# existing reply pattern; an answer points at a question, a subagent_reply
# points at a subagent_call, etc.).
REPLY_LIKE_TYPES = {"reply", "subagent_reply", "answer"}

ID_RE = re.compile(r"^MSG-\d{8}-\d{6}-[0-9a-f]{6}$")
FILENAME_RE = re.compile(r"^(MSG-\d{8}-\d{6}-[0-9a-f]{6})\.md$")
STALE_THRESHOLD_HOURS = 24


def load_frontmatter(path: Path) -> tuple[dict | None, str]:
    """Return (frontmatter dict, error reason). dict is None on parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"unreadable: {exc}"
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, "missing opening --- delimiter"
    rest = text.split("---", 2)
    if len(rest) < 3:
        return None, "missing closing --- delimiter"
    body = rest[1]
    meta: dict[str, object] = {}
    current_list_key: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line:
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            existing = meta.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(line[4:].strip())
            continue
        if ":" not in line:
            return None, f"malformed line (no colon): {line!r}"
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            meta[key] = []
            current_list_key = key
        else:
            if value == "[]":
                meta[key] = []
            else:
                meta[key] = value
            current_list_key = None
    return meta, ""


def parse_iso8601(value: str) -> bool:
    try:
        normalized = value.replace("Z", "+00:00")
        _dt.datetime.fromisoformat(normalized)
        return True
    except Exception:
        return False


def hours_since(ts: str, now: _dt.datetime | None = None) -> float | None:
    try:
        normalized = ts.replace("Z", "+00:00")
        when = _dt.datetime.fromisoformat(normalized)
        ref = now or _dt.datetime.now(when.tzinfo or _dt.timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=ref.tzinfo)
        return (ref - when).total_seconds() / 3600.0
    except Exception:
        return None


def _rel(path: Path) -> str:
    """Repo-relative path for display, falling back to the absolute path when
    `path` is outside REPO_ROOT (e.g. a tmpdir under test). `relative_to` raises
    ValueError on out-of-repo paths, which would otherwise crash the linter."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def collect_message_files() -> list[Path]:
    files: list[Path] = []
    for sub in SCAN_DIRS:
        d = MESSAGES_DIR / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name == ".gitkeep" or p.is_dir():
                continue
            if p.suffix != ".md":
                continue
            files.append(p)
    return files


def lint() -> tuple[int, int]:
    errors = 0
    warnings = 0
    files = collect_message_files()
    parsed: dict[str, tuple[Path, dict]] = {}

    for path in files:
        meta, err = load_frontmatter(path)
        if meta is None:
            print(f"ERROR: {_rel(path)}: {err}")
            errors += 1
            continue

        m = FILENAME_RE.match(path.name)
        if not m:
            print(f"ERROR: {_rel(path)}: filename does not match MSG-YYYYMMDD-HHMMSS-{{6 hex}}.md")
            errors += 1
            continue
        filename_id = m.group(1)

        for field in REQUIRED_FIELDS:
            if field not in meta:
                print(f"ERROR: {_rel(path)}: required field '{field}' missing")
                errors += 1

        msg_id = meta.get("id", "")
        if not isinstance(msg_id, str) or not ID_RE.match(msg_id):
            print(f"ERROR: {_rel(path)}: id '{msg_id}' is not in MSG-YYYYMMDD-HHMMSS-{{6 hex}} format")
            errors += 1
        elif msg_id != filename_id:
            print(f"ERROR: {_rel(path)}: id '{msg_id}' does not match filename '{filename_id}'")
            errors += 1

        msg_type = meta.get("type", "")
        if msg_type not in TYPE_ENUM:
            print(f"ERROR: {_rel(path)}: type '{msg_type}' not in {sorted(TYPE_ENUM)}")
            errors += 1

        status = meta.get("status", "")
        if status not in STATUS_ENUM:
            print(f"ERROR: {_rel(path)}: status '{status}' not in {sorted(STATUS_ENUM)}")
            errors += 1

        ts = meta.get("ts", "")
        if not isinstance(ts, str) or not parse_iso8601(ts):
            print(f"ERROR: {_rel(path)}: ts '{ts}' is not parseable ISO 8601")
            errors += 1

        if msg_type in REPLY_LIKE_TYPES:
            irt = meta.get("in_reply_to")
            if not irt or (isinstance(irt, list) and not irt):
                print(f"ERROR: {_rel(path)}: type={msg_type} requires non-empty in_reply_to")
                errors += 1

        # TASK-119 — question must declare who is expected to answer.
        if msg_type == "question":
            qf = meta.get("question_for")
            if not qf or (isinstance(qf, list) and not qf):
                print(f"ERROR: {_rel(path)}: type=question requires non-empty question_for")
                errors += 1

        if isinstance(msg_id, str) and ID_RE.match(msg_id):
            if msg_id in parsed:
                prior = _rel(parsed[msg_id][0])
                print(f"ERROR: {_rel(path)}: duplicate id '{msg_id}' (also in {prior})")
                errors += 1
            else:
                parsed[msg_id] = (path, meta)

    # cross-message checks
    claims_by_parent: dict[str, list[str]] = {}
    for msg_id, (path, meta) in parsed.items():
        irt = meta.get("in_reply_to") or ""
        if isinstance(irt, str) and irt and irt not in parsed:
            print(f"ERROR: {_rel(path)}: in_reply_to '{irt}' references unknown id")
            errors += 1
        if meta.get("status") == "claimed" and isinstance(irt, str) and irt:
            claims_by_parent.setdefault(irt, []).append(msg_id)
        if meta.get("status") == "open" and "samples" not in path.parts:
            age = hours_since(meta.get("ts", ""))
            if age is not None and age > STALE_THRESHOLD_HOURS:
                print(f"WARN: {_rel(path)}: open message stale ({age:.1f}h > {STALE_THRESHOLD_HOURS}h)")
                warnings += 1
        if meta.get("status") == "archived":
            # Heuristic: archive should follow answered/blocked. We only check if there
            # is at least one prior answered/blocked reply pointing at this message.
            if not any(
                m2.get("in_reply_to") == msg_id and m2.get("status") in {"answered", "blocked"}
                for _id, (_p, m2) in parsed.items()
            ):
                print(f"WARN: {_rel(path)}: archived without prior answered/blocked reply")
                warnings += 1

    for parent, claimers in claims_by_parent.items():
        if len(claimers) > 1:
            print(f"ERROR: parent '{parent}' has duplicate claimed records from {claimers}")
            errors += 1

    print(f"\n{'FAILED' if errors else 'OK'}: {errors} error(s), {warnings} warning(s)")
    return errors, warnings


def main() -> int:
    if not MESSAGES_DIR.is_dir():
        print(f"agents/messages not found at {MESSAGES_DIR}")
        return 2
    errors, _ = lint()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
