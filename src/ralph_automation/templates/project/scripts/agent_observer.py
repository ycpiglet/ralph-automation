#!/usr/bin/env python3
"""Agent Observer — read-only runtime view (TASK-103).

AGENT_RUNTIME.md §7 final milestone: "Panes should be views, not the source of
truth. The runtime state should live in files/state/event logs, not in the pane."

This is the proof of that rule. The observer reconstructs an agent's status
*purely by reading runtime files* — it never hosts a worker and never writes
anything. A worker may run in another pane, in the background, or on another
machine; the observer reflects its state from:

  - agents/runtime/events/<role>-<date>.jsonl   (worker event log, TASK-099/102)
  - agents/messages/inbox/                       (message statuses)
  - agents/runtime/sessions/*.json               (optional session state)

Modes:
  --once    one snapshot then exit (CI / tests)
  --watch   poll and re-render until stopped (pane default)
  --json    machine-readable snapshot (test contract)

Read-only invariant: the observer mutates no file. Enforced by tests.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
MESSAGES_INBOX = REPO_ROOT / "agents" / "messages" / "inbox"
EVENTS_DIR = REPO_ROOT / "agents" / "runtime" / "events"
SESSIONS_DIR = REPO_ROOT / "agents" / "runtime" / "sessions"

# Mirror agent_worker.ROLE_ALIASES so observer and worker accept the same roles.
ROLE_ALIASES = {
    "qa": "qa",
    "lead": "lead-engineer",
    "lead-engineer": "lead-engineer",
    "backend": "backend",
    "ci-cd": "ci-cd",
    "cicd": "ci-cd",
    "uiux": "uiux",
    "beta": "beta-tester",
    "beta-tester": "beta-tester",
    "ceo": "ceo",
    "managing-partner": "managing-partner",
    "independent-auditor": "independent-auditor",
    "doc": "doc-steward",
    "doc-steward": "doc-steward",
    "steward": "doc-steward",
    "scribe": "scribe",
    "archivist": "scribe",
    "research": "research",
    "research-agent": "research",
    "researcher": "research",
    "timeline": "timeline",
    "timeline-agent": "timeline",
    "chronology": "timeline",
}


def normalize_role(raw: str) -> str:
    key = raw.strip().lstrip("/").lower().replace("_", "-")
    if key not in ROLE_ALIASES:
        known = ", ".join(sorted(set(ROLE_ALIASES.values())))
        raise SystemExit(f"unknown role '{raw}'. known: {known}")
    return ROLE_ALIASES[key]


def date_today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------- read-only sources ----------

def read_events(events_dir: Path, role: str, date: str | None = None) -> list[dict]:
    """Return parsed event records for role on a given date (today by default).

    If today's file is absent, fall back to the most recent <role>-*.jsonl so a
    pane opened the morning after a run still shows the last known state.
    """
    date = date or date_today()
    path = events_dir / f"{role}-{date}.jsonl"
    if not path.is_file():
        candidates = sorted(events_dir.glob(f"{role}-*.jsonl")) if events_dir.is_dir() else []
        if not candidates:
            return []
        path = candidates[-1]
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return records


def _parse_frontmatter_min(text: str) -> dict:
    """Minimal frontmatter reader (read-only). Returns {} when absent."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta: dict[str, str] = {}
    for raw in parts[1].splitlines():
        line = raw.rstrip()
        if not line or ":" not in line or line.startswith("  - "):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def read_inbox(inbox: Path, role: str) -> list[dict]:
    """Return message frontmatter dicts touching role (to or from), oldest first."""
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(inbox.iterdir()):
        if p.suffix != ".md" or p.name.startswith("."):
            continue
        try:
            meta = _parse_frontmatter_min(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not meta:
            continue
        if meta.get("to") == role or meta.get("from") == role:
            out.append(meta)
    return out


# ---------- pipeline-wide read-only sources (TASK-112) ----------

def read_all_events(events_dir: Path, date: str | None = None) -> list[dict]:
    """Merge every role's event log for `date` (today by default), sorted by ts.

    Pipeline views span roles (backend/qa/ci-cd), so unlike read_events this
    reads all <role>-<date>.jsonl files. Read-only.
    """
    date = date or date_today()
    if not events_dir.is_dir():
        return []
    records: list[dict] = []
    for path in sorted(events_dir.glob(f"*-{date}.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    records.sort(key=lambda r: r.get("ts", ""))
    return records


def read_pipeline_messages(inbox: Path, pipeline_name: str) -> list[dict]:
    """Return frontmatter dicts of inbox messages tagged with this pipeline."""
    if not inbox.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(inbox.iterdir()):
        if p.suffix != ".md" or p.name.startswith("."):
            continue
        try:
            meta = _parse_frontmatter_min(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if meta.get("pipeline") == pipeline_name:
            out.append(meta)
    return out


# ---------- status derivation ----------

@dataclass
class Snapshot:
    role: str
    status: str = "unknown"
    current_task: str | None = None
    latest_message: dict | None = None
    provider: str | None = None
    last_reply_chars: int | None = None
    last_error: str | None = None
    recent_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "status": self.status,
            "current_task": self.current_task,
            "latest_message": self.latest_message,
            "provider": self.provider,
            "last_reply_chars": self.last_reply_chars,
            "last_error": self.last_error,
            "recent_events": self.recent_events,
        }


@dataclass
class PipelineSnapshot:
    pipeline: str
    status: str = "unknown"          # running | complete | halt | unknown
    active_stage: str | None = None
    active_role: str | None = None
    loopbacks: int = 0
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "status": self.status,
            "active_stage": self.active_stage,
            "active_role": self.active_role,
            "loopbacks": self.loopbacks,
            "history": self.history,
        }


def derive_pipeline_snapshot(pipeline_name: str, events: list[dict],
                             messages: list[dict]) -> PipelineSnapshot:
    """Reconstruct a pipeline's progress from pipeline_advanced events + tagged
    inbox messages. Pure / no side effects (read-only)."""
    snap = PipelineSnapshot(pipeline=pipeline_name)

    adv = [e for e in events
           if e.get("event") == "pipeline_advanced" and e.get("pipeline") == pipeline_name]
    snap.history = [{"ts": e.get("ts"), "from": e.get("role"), "to": e.get("to"),
                     "to_stage": e.get("to_stage"), "kind": e.get("kind")} for e in adv]

    status = "unknown"
    for e in adv:
        if e.get("kind") in ("complete", "halt"):
            status = e["kind"]
    if status == "unknown" and (adv or messages):
        status = "running"
    snap.status = status

    snap.loopbacks = max((int(m.get("loopbacks", 0) or 0) for m in messages), default=0)

    if status == "running":
        open_reqs = [m for m in messages
                     if m.get("type") == "request" and m.get("status") in ("open", "claimed")]
        if open_reqs:
            active = open_reqs[-1]
            snap.active_stage = active.get("stage")
            snap.active_role = active.get("to")
    return snap


def _last(events: list[dict], name: str) -> dict | None:
    for rec in reversed(events):
        if rec.get("event") == name:
            return rec
    return None


def derive_snapshot(role: str, events: list[dict], inbox: list[dict],
                    tail: int = 8) -> Snapshot:
    """Reconstruct agent status from event log + inbox. Pure / no side effects.

    Status precedence (last meaningful signal wins):
      stopped  — last event is worker_stopped
      error    — most recent provider_error after the last claim
      blocked  — an inbox message addressed to role has status 'blocked'
      working  — a message was claimed but not yet answered
      idle     — worker started/polling, nothing in flight
    """
    snap = Snapshot(role=role)
    snap.recent_events = events[-tail:]

    # provider / reply info from the last provider_called
    pc = _last(events, "provider_called")
    if pc:
        snap.provider = pc.get("provider")
        snap.last_reply_chars = pc.get("reply_chars")

    # current task + latest message from inbox addressed to role (requests only)
    inbound = [m for m in inbox if m.get("to") == role and m.get("type") != "reply"]
    if inbound:
        latest = inbound[-1]
        snap.latest_message = {
            "id": latest.get("id"),
            "from": latest.get("from"),
            "intent": latest.get("intent"),
            "status": latest.get("status"),
        }
        snap.current_task = latest.get("task_id") if latest.get("task_id") not in (None, "none") else None

    # ----- status -----
    if not events:
        snap.status = "idle" if inbound else "offline"
        return snap

    last_event = events[-1].get("event")
    if last_event == "worker_stopped":
        snap.status = "stopped"
        stopped = events[-1]
        if stopped.get("reason") == "provider_error":
            snap.last_error = "worker stopped after provider error"
        return snap

    # error: a provider_error not yet followed by a successful reply
    err = _last(events, "provider_error")
    if err:
        # find index of last reply_written; error is current only if it's later
        last_err_idx = max(i for i, e in enumerate(events) if e.get("event") == "provider_error")
        last_ok_idx = max((i for i, e in enumerate(events)
                           if e.get("event") == "provider_called"), default=-1)
        if last_err_idx > last_ok_idx:
            snap.status = "error"
            snap.last_error = f"{err.get('error_type')}: {err.get('error')}"
            return snap

    # blocked: any inbound message explicitly blocked
    if any(m.get("status") == "blocked" for m in inbound):
        snap.status = "blocked"
        return snap

    # working: claimed without a following answered/stopped
    last_claim_idx = max((i for i, e in enumerate(events)
                          if e.get("event") == "message_claimed"), default=-1)
    last_done_idx = max((i for i, e in enumerate(events)
                         if e.get("event") in ("status_updated", "reply_written")), default=-1)
    if last_claim_idx > last_done_idx:
        snap.status = "working"
        return snap

    snap.status = "idle"
    return snap


# ---------- rendering ----------

def render_text(snap: Snapshot) -> str:
    lines = []
    lines.append("=" * 56)
    lines.append(f"Agent Observer — {snap.role}  (read-only, TASK-103)")
    lines.append("=" * 56)
    lines.append(f"  status        : {snap.status}")
    lines.append(f"  current task  : {snap.current_task or '-'}")
    if snap.latest_message:
        m = snap.latest_message
        lines.append(f"  latest message: {m.get('id')} from {m.get('from')} "
                     f"[{m.get('status')}] — {m.get('intent')}")
    else:
        lines.append("  latest message: -")
    lines.append(f"  provider      : {snap.provider or '-'}"
                 + (f" (last reply {snap.last_reply_chars} chars)"
                    if snap.last_reply_chars is not None else ""))
    lines.append(f"  last error    : {snap.last_error or 'none'}")
    lines.append("  recent events :")
    if snap.recent_events:
        for e in snap.recent_events:
            extra = ""
            if e.get("event") == "provider_called":
                extra = f" provider={e.get('provider')} chars={e.get('reply_chars')}"
            elif e.get("event") == "message_claimed":
                extra = f" {e.get('message_id')}"
            elif e.get("event") == "provider_error":
                extra = f" {e.get('error_type')}"
            elif e.get("event") == "worker_stopped":
                extra = f" reason={e.get('reason')}"
            lines.append(f"    [{e.get('ts')}] {e.get('event')}{extra}")
    else:
        lines.append("    (no events yet)")
    return "\n".join(lines)


def render_pipeline(snap: PipelineSnapshot) -> str:
    lines = []
    lines.append("=" * 56)
    lines.append(f"Pipeline Observer — {snap.pipeline}  (read-only, TASK-112)")
    lines.append("=" * 56)
    lines.append(f"  status      : {snap.status}")
    lines.append(f"  active stage: {snap.active_stage or '-'}"
                 + (f" → {snap.active_role}" if snap.active_role else ""))
    lines.append(f"  loopbacks   : {snap.loopbacks}")
    lines.append("  handoffs    :")
    if snap.history:
        for h in snap.history:
            lines.append(f"    [{h.get('ts')}] {h.get('from')} → {h.get('to')} "
                         f"({h.get('to_stage')}) {h.get('kind')}")
    else:
        lines.append("    (no handoffs yet)")
    return "\n".join(lines)


def snapshot_is_terminal(snap) -> bool:
    """True when the watched work is finished — used by --exit-on-stop.
    Role view: the worker stopped. Pipeline view: pipeline completed or halted."""
    if isinstance(snap, PipelineSnapshot):
        return snap.status in ("complete", "halt")
    return getattr(snap, "status", None) == "stopped"


def build_snapshot(role: str, tail: int = 8) -> Snapshot:
    events = read_events(EVENTS_DIR, role)
    inbox = read_inbox(MESSAGES_INBOX, role)
    return derive_snapshot(role, events, inbox, tail=tail)


def build_pipeline_snapshot(pipeline_name: str) -> PipelineSnapshot:
    events = read_all_events(EVENTS_DIR)
    messages = read_pipeline_messages(MESSAGES_INBOX, pipeline_name)
    return derive_pipeline_snapshot(pipeline_name, events, messages)


# ---------- fs watcher (TASK-106, opt-in) ----------

def start_runtime_watcher(signal: threading.Event, log_fn=None):
    """Subscribe to runtime file changes (inbox + event log) via watchdog.

    Observer state comes from both MESSAGES_INBOX (message statuses) and
    EVENTS_DIR (worker activity), so we watch both. On any change, set `signal`
    to wake the watch loop for an immediate re-render. Returns the started
    Observer, or None if watchdog is unavailable (loop falls back to polling).
    Read-only: the watcher only triggers reads — the observer still writes nothing.
    Thin wrapper over the shared fs_watch helper (TASK-107), bound to both the
    inbox and event-log directories (observer state comes from both).
    """
    from fs_watch import start_fs_watcher
    return start_fs_watcher([MESSAGES_INBOX, EVENTS_DIR], signal, log_fn)


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_observer",
        description="Read-only runtime view of an agent (TASK-103).",
    )
    parser.add_argument("--role", required=False,
                        help=f"role to observe. one of: {sorted(set(ROLE_ALIASES.values()))}")
    parser.add_argument("--pipeline",
                        help="observe a pipeline's progress instead of a single role "
                             "(e.g. --pipeline build). Mutually exclusive with --role.")
    parser.add_argument("--once", action="store_true",
                        help="render a single snapshot then exit (default if --watch absent)")
    parser.add_argument("--watch", action="store_true",
                        help="poll and re-render until interrupted (pane default)")
    parser.add_argument("--watch-fs", action="store_true",
                        help="TASK-106: re-render on inbox/event-log fs changes via "
                             "watchdog (opt-in, implies --watch). watchdog 미설치/미지정 시 "
                             "--interval 폴링 fallback")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between re-renders in --watch / fallback in --watch-fs (default 1.0)")
    parser.add_argument("--tail", type=int, default=8,
                        help="number of recent events to show (default 8)")
    parser.add_argument("--json", action="store_true",
                        help="emit the snapshot as JSON (machine-readable)")
    parser.add_argument("--exit-on-stop", action="store_true",
                        help="in --watch/--watch-fs, exit (return 0) once the watched work "
                             "is finished (role: worker_stopped; pipeline: complete/halt). "
                             "Opt-in; default keeps the view alive (AGENT_RUNTIME §7).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.role and not args.pipeline:
        raise SystemExit("one of --role or --pipeline is required")
    role = normalize_role(args.role) if args.role else None

    def emit():
        if args.pipeline:
            snap = build_pipeline_snapshot(args.pipeline)
            payload = snap.to_dict()
            text = render_pipeline(snap)
        else:
            snap = build_snapshot(role, tail=max(args.tail, 0))
            payload = snap.to_dict()
            text = render_text(snap)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        else:
            print(text, flush=True)
        return snap

    # --watch-fs implies a continuous watch loop.
    if not args.watch and not args.watch_fs:
        snap = emit()
        return 0

    interval = max(args.interval, 0.1)
    # TASK-106: optional fs-event-driven re-render. Polling is the fallback so
    # the observer still re-renders on a timer if events are missed/unavailable.
    signal = threading.Event()
    observer = None
    if args.watch_fs:
        observer = start_runtime_watcher(signal, lambda m: print(f"[observer] {m}", file=sys.stderr))
    try:
        while True:
            if not args.json:
                # clear-ish separation between frames without clearing scrollback
                print("\n", flush=True)
            snap = emit()
            if args.exit_on_stop and snapshot_is_terminal(snap):
                return 0
            if observer is not None:
                signal.wait(timeout=interval)
                signal.clear()
            else:
                time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())
