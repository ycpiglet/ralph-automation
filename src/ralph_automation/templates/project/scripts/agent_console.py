#!/usr/bin/env python3
"""Game-console style agent dashboard (TASK-122, 1st-cut prototype).

A read-only, stdlib-only ANSI console that renders the agent runtime as a
single "game console" screen with three panels:

    Online Agents   — live worker sessions (agents/runtime/sessions/*.json)
    Team Chat       — recent message-bus traffic (agents/messages/inbox/*.md)
    Quest Board     — waiting TASKs by priority (agents/lead_engineer/tasks/)

Design (CEO decision 2026-05-27, AskUserQuestion): pure Python stdlib, zero
new dependencies, reusing existing runtime data. The richer textual/web
console is a follow-up (see STAGE-7-GUI.md). The render is a pure function so
it is unit-testable; loaders are best-effort and never raise.

CLI:
  python scripts/agent_console.py            # one snapshot
  python scripts/agent_console.py --watch --interval 2   # refresh loop
  python scripts/agent_console.py --width 100
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "agents" / "runtime"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
INBOX_DIR = ROOT / "agents" / "messages" / "inbox"
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"
EVENTS_DIR = RUNTIME_DIR / "events"

ACTIVE_STATES = {"spawning", "active"}
PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


# ---------- best-effort loaders (never raise) ----------


def _read_frontmatter(path: Path) -> dict:
    """Minimal frontmatter reader shared by message + task loaders."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta: dict[str, str] = {}
    for raw in parts[1].splitlines():
        line = raw.strip()
        if not line or ":" not in line or line.startswith("- "):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def load_sessions() -> list[dict]:
    out: list[dict] = []
    if not SESSIONS_DIR.is_dir():
        return out
    for p in sorted(SESSIONS_DIR.iterdir()):
        if p.suffix != ".json" or p.name.startswith("."):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "agent_id": rec.get("agent_id", "?"),
            "role": rec.get("role", "?"),
            "status": rec.get("status", "?"),
            "task_id": rec.get("task_id", "none"),
        })
    return out


def load_recent_messages(limit: int = 8) -> list[dict]:
    out: list[dict] = []
    if not INBOX_DIR.is_dir():
        return out
    files = [p for p in INBOX_DIR.iterdir()
             if p.suffix == ".md" and not p.name.startswith(".")]
    # filename embeds MSG-YYYYMMDD-HHMMSS-... so name sort == chronological
    for p in sorted(files)[-limit:]:
        m = _read_frontmatter(p)
        if not m:
            continue
        out.append({
            "from": m.get("from", "?"),
            "to": m.get("to", "?"),
            "type": m.get("type", "?"),
            "intent": m.get("intent", ""),
            "ts": m.get("ts", ""),
        })
    return out


def load_waiting_tasks(limit: int = 8) -> list[dict]:
    out: list[dict] = []
    if not TASKS_DIR.is_dir():
        return out
    for p in sorted(TASKS_DIR.iterdir()):
        if p.suffix != ".md" or not p.name.startswith("TASK-"):
            continue
        m = _read_frontmatter(p)
        if m.get("status") != "대기":
            continue
        out.append({
            "id": m.get("id", p.stem),
            "priority": m.get("priority", "?"),
            "owner": m.get("owner", "?"),
        })
    out.sort(key=lambda t: (PRIORITY_ORDER.get(t["priority"], 99), t["id"]))
    return out[:limit]


def load_recent_collabs(limit: int = 6) -> list[dict]:
    """TASK-126 — recent collaboration events (collab-<date>.jsonl), best-effort."""
    out: list[dict] = []
    if not EVENTS_DIR.is_dir():
        return out
    for p in sorted(EVENTS_DIR.glob("collab-*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append({
                    "task_id": rec.get("task_id", "?"),
                    "tier": rec.get("tier", "?"),
                    "method": rec.get("method", "?"),
                    "verdict": rec.get("verdict", "?"),
                    "tokens": rec.get("tokens"),
                })
        except OSError:
            continue
    return out[-limit:]


# ---------- pure rendering ----------


def _clip(s: str, width: int) -> str:
    if width <= 1:
        return ""
    return s if len(s) <= width else s[: width - 1] + "…"


def render_panel(title: str, lines: list[str], width: int) -> list[str]:
    """Render a boxed panel as a list of fixed-width rows (pure)."""
    width = max(width, len(title) + 6, 12)
    inner = width - 2
    # "┌─ {title} " prefix is 4 + len(title) cells; fill to width-1 then "┐".
    dashes = max(0, width - len(title) - 5)
    top = "┌─ " + title + " " + "─" * dashes + "┐"
    body_rows = lines if lines else ["(none)"]
    out = [top]
    for ln in body_rows:
        cell = _clip(ln, inner - 1)
        out.append("│ " + cell + " " * (inner - 1 - len(cell)) + "│")
    out.append("└" + "─" * inner + "┘")
    return out


def render_console(sessions: list[dict], messages: list[dict],
                   tasks: list[dict], width: int = 72,
                   now: str | None = None,
                   collabs: list[dict] | None = None) -> str:
    """Render the full console as one string (pure, testable).

    TASK-126 — adds a Collaboration panel when `collabs` is provided (live
    observation of subagent collaboration: tier / method / verdict / tokens).
    """
    ts = now or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    header = f"═══ AGENT CONSOLE ═══  {ts}"

    agent_lines = [
        f"{s['role']:<14} {s['status']:<9} {s['task_id']}"
        for s in sessions
    ] or ["no live sessions"]

    chat_lines = [
        f"{m['from']}→{m['to']} [{m['type']}] {m['intent']}"
        for m in messages
    ] or ["no recent messages"]

    quest_lines = [
        f"[{t['priority']}] {t['id']} ({t['owner']})"
        for t in tasks
    ] or ["no waiting TASKs"]

    parts = [header, ""]
    parts += render_panel(f"Online Agents ({len(sessions)})", agent_lines, width)
    parts.append("")
    parts += render_panel(f"Team Chat ({len(messages)})", chat_lines, width)
    parts.append("")
    parts += render_panel(f"Quest Board ({len(tasks)})", quest_lines, width)
    if collabs is not None:
        collab_lines = [
            f"{c['task_id']} [{c.get('tier','?')}/{c.get('method','?')}] "
            f"-> {c.get('verdict','?')}"
            + (f" {c['tokens']}tok" if c.get("tokens") else "")
            for c in collabs
        ] or ["no collaborations yet"]
        parts.append("")
        parts += render_panel(f"Collaboration ({len(collabs)})", collab_lines, width)
    return "\n".join(parts) + "\n"


def snapshot(width: int = 72) -> str:
    return render_console(
        load_sessions(), load_recent_messages(), load_waiting_tasks(),
        width=width, collabs=load_recent_collabs(),
    )


# ---------- CLI ----------

CLEAR = "\033[2J\033[H"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agent_console.py",
        description="Game-console style agent dashboard (TASK-122, read-only).",
    )
    p.add_argument("--width", type=int, default=72, help="panel width (default 72)")
    p.add_argument("--watch", action="store_true",
                   help="refresh loop (Ctrl-C to exit)")
    p.add_argument("--interval", type=float, default=2.0,
                   help="watch refresh seconds (default 2.0)")
    p.add_argument("--once", action="store_true",
                   help="(default) print one snapshot and exit")
    p.add_argument("--exit-on-stop", action="store_true",
                   help="TASK-127: watch 루프가 stop-file 존재 시 자동 종료 "
                        "(작업 종료 시 관찰 화면도 사라짐 — 게임 콘솔 비전)")
    p.add_argument("--stop-file", default=str(RUNTIME_DIR / "STOP_LOOP"),
                   help="--exit-on-stop 의 stop-file 경로")
    args = p.parse_args(argv)

    if not args.watch:
        sys.stdout.write(snapshot(width=args.width))
        return 0

    stop_path = Path(args.stop_file)
    try:
        while True:
            if args.exit_on_stop and stop_path.exists():
                sys.stdout.write(f"\nagent_console: stop-file 감지 ({stop_path.name}) — 종료.\n")
                return 0
            sys.stdout.write(CLEAR + snapshot(width=args.width))
            sys.stdout.flush()
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        sys.stdout.write("\nagent_console: stopped.\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
