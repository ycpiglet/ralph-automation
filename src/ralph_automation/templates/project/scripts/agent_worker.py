#!/usr/bin/env python3
"""Agent Worker Runtime Loop (TASK-099).

A long-running process that represents one agent role and autonomously
processes file-inbox messages addressed to it. The worker is the missing
runtime piece diagnosed by AGENT_RUNTIME.md: pane != agent, worker = agent,
pane = camera view, LLM = provider.

Loop:
  1. Poll agents/messages/inbox/ for `to: <role>, status: open` messages
  2. Claim the oldest matching message (status: open -> claimed)
  3. Call provider.run(role, instruction, context) -> reply text
  4. Write a reply message file (type: reply, in_reply_to: <original>)
  5. Mark original message status: answered
  6. Append five event types to agents/runtime/events/<role>-<date>.jsonl
  7. Sleep poll_interval and continue

Stop conditions: stop-file exists, timeout exceeded, KeyboardInterrupt.

TASK-099 ships DummyProvider only. ClaudeProvider arrives in TASK-102.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import get_provider, Provider, ProviderError  # noqa: E402
import model_routing  # noqa: E402
import eval_harness  # noqa: E402

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
MESSAGES_INBOX = REPO_ROOT / "agents" / "messages" / "inbox"
EVENTS_DIR = REPO_ROOT / "agents" / "runtime" / "events"
STOP_DIR = REPO_ROOT / "agents" / "runtime" / "stop"

# TASK-108: load .env so providers that need config (e.g. ClaudeProvider sdk
# backend: CLAUDE_PROVIDER_BACKEND / ANTHROPIC_API_KEY) work when the worker runs
# in a fresh pane. Non-override: real environment variables still win. No-op if
# python-dotenv is absent (dummy/cli users need no config).
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

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


# ---------- timestamp helpers ----------

def ts_now_iso() -> str:
    t = time.localtime()
    offset_minutes = -time.altzone // 60 if t.tm_isdst else -time.timezone // 60
    sign = "+" if offset_minutes >= 0 else "-"
    offset = f"{sign}{abs(offset_minutes)//60:02d}:{abs(offset_minutes)%60:02d}"
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + offset


def ts_compact() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def date_today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------- frontmatter parsing/serializing ----------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body str). Mirrors agent_orchestrator.parse_frontmatter
    but also returns body for round-trip writes.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, object] = {}
    current_list_key: str | None = None
    for raw in parts[1].splitlines():
        line = raw.rstrip()
        if not line:
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            existing = meta.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(line[4:].strip())
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            meta[key] = []
            current_list_key = key
        else:
            meta[key] = value
            current_list_key = None
    body = parts[2].lstrip("\n")
    return meta, body


def serialize_frontmatter(meta: dict, body: str) -> str:
    """Round-trip frontmatter back to the canonical inbox schema order.

    Order matches the format produced by agent_orchestrator.cmd_call so a
    re-saved message remains diff-friendly.
    """
    keys_in_order = ["id", "from", "to", "task_id", "intent", "type",
                     "status", "ts", "in_reply_to", "evidence", "next"]
    lines = ["---"]
    for k in keys_in_order:
        if k not in meta:
            continue
        v = meta[k]
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        else:
            # Empty in_reply_to is rendered as "in_reply_to:" (no value)
            if v == "" or v is None:
                lines.append(f"{k}:")
            else:
                lines.append(f"{k}: {v}")
    # Preserve any extra keys we didn't anticipate
    for k, v in meta.items():
        if k in keys_in_order:
            continue
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body


def atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ---------- event log ----------

def append_event(events_dir: Path, role: str, event: str, **fields) -> None:
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / f"{role}-{date_today()}.jsonl"
    record = {"ts": ts_now_iso(), "role": role, "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- worker loop ----------

@dataclass
class WorkerConfig:
    role: str
    provider_name: str
    poll_interval: float = 1.0
    timeout: float = 0.0          # 0 = infinite
    stop_file: Path | None = None
    once: bool = False            # process at most one message then exit (for tests)
    verbose: bool = True
    watch_fs: bool = False        # TASK-105: event-driven inbox via watchdog (opt-in)
    handled_ids: set[str] = field(default_factory=set)
    routing_decision: dict | None = None
    routing_model: str | None = None
    routing_grade: str | None = None
    routing_prompt: str = ""
    routing_changed_files: list[str] | None = None
    routing_diff_lines: int = 0
    eval_log_path: Path | None = None


def apply_model_routing_env(
    provider_name: str,
    *,
    model: str | None = None,
    grade: str | None = None,
    prompt: str = "",
    changed_files: list[str] | None = None,
    diff_lines: int = 0,
) -> dict | None:
    """Set provider env vars for a routed worker model and return the decision."""
    if not model:
        return None
    if str(model).strip().lower() == "auto":
        # Long-running workers must route per message; a startup auto decision
        # would pin the first grade/prompt across later inbox items.
        return None
    decision = model_routing.resolve_model(
        model,
        grade=grade,
        prompt=prompt,
        changed_files=changed_files,
        diff_lines=diff_lines,
    )
    for name, value in model_routing.provider_env(provider_name, decision["selected_tier"]).items():
        os.environ[name] = value
    return decision


def routing_event_fields(decision: dict | None) -> dict:
    if not decision:
        return {}
    return {
        "routing_grade": decision["grade"],
        "policy_model": decision["policy_tier"],
        "selected_model": decision["selected_tier"],
        "routing_signals": list(decision.get("signals") or []),
        "routing_reason": decision.get("reason", ""),
    }


def _as_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _int_or_zero(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _message_routing_decision(cfg: WorkerConfig, meta: dict, instruction: str) -> dict | None:
    if cfg.routing_decision and str(cfg.routing_model or "").strip().lower() != "auto":
        return cfg.routing_decision
    model = meta.get("routing_model") or meta.get("model") or cfg.routing_model
    if not model:
        return None
    grade = meta.get("routing_grade") or meta.get("grade") or cfg.routing_grade
    changed_files = _as_list(meta.get("routing_changed_files")) or (cfg.routing_changed_files or [])
    diff_lines = _int_or_zero(meta.get("routing_diff_lines")) or cfg.routing_diff_lines
    prompt = str(meta.get("intent") or cfg.routing_prompt or instruction)
    return model_routing.resolve_model(
        str(model),
        grade=str(grade or "Medium"),
        prompt=prompt,
        changed_files=changed_files,
        diff_lines=diff_lines,
    )


def _apply_routing_to_provider(provider: Provider, provider_name: str, decision: dict | None) -> None:
    if not decision:
        return
    for name, value in model_routing.provider_env(provider_name, decision["selected_tier"]).items():
        os.environ[name] = value
        if name == "CLAUDE_AGENT_MODEL" and hasattr(provider, "model"):
            setattr(provider, "model", value)


def _baseline_tokens(meta: dict) -> int | None:
    for key in ("eval_baseline_tokens", "baseline_tokens"):
        value = meta.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _metadata_task_id(meta: dict) -> str:
    task_id = str(meta.get("task_id") or "").strip()
    if task_id and task_id.lower() not in {"none", "unknown", "null"}:
        return task_id
    return str(meta.get("id") or "none")


def _routing_eval_skip_reason(provider: Provider, provider_name: str, routing_decision: dict | None) -> str | None:
    if not routing_decision:
        return None
    selected = str(routing_decision.get("selected_tier") or "").lower()
    if not selected:
        return "routing_not_applied"
    if model_routing.provider_env(provider_name, routing_decision["selected_tier"]):
        return None
    provider_model = str(getattr(provider, "model", None) or "").lower()
    if provider_model == selected:
        return None
    if selected in {"haiku", "sonnet", "opus"} and selected in provider_model:
        return None
    return "routing_not_applied"


def _record_eval_outcome(
    cfg: WorkerConfig,
    meta: dict,
    provider: Provider,
    routing_decision: dict | None,
    result=None,
    *,
    tokens: int | None = None,
    finish_reason: str | None = None,
    error=None,
    actual_tokens_known: bool | None = None,
) -> tuple[bool, str | None]:
    if not routing_decision:
        return False, None
    skip_reason = _routing_eval_skip_reason(provider, cfg.provider_name, routing_decision)
    if skip_reason:
        return False, skip_reason
    baseline = _baseline_tokens(meta)
    if baseline is None:
        return False, None
    if result is not None:
        tokens = int(getattr(result, "tokens_in", 0) or 0) + int(getattr(result, "tokens_out", 0) or 0)
        finish_reason = str(getattr(result, "finish_reason", "stop") or "stop")
        error = getattr(result, "error", None)
    tokens = int(tokens or 0)
    if actual_tokens_known is None:
        actual_tokens_known = tokens > 0
    eval_harness.record_outcome(
        _metadata_task_id(meta),
        routing_decision["grade"],
        str(getattr(provider, "model", None) or routing_decision["selected_tier"]),
        tokens,
        finish_reason=str(finish_reason or "stop"),
        outcome="ok" if not error else "gate-error",
        path=cfg.eval_log_path or eval_harness.EVAL_LOG,
        policy_model=routing_decision["policy_tier"],
        selected_model=routing_decision["selected_tier"],
        routing_signals=list(routing_decision.get("signals") or []),
        baseline_tokens=baseline,
        actual_tokens_known=actual_tokens_known,
    )
    return True, None


def normalize_role(raw: str) -> str:
    key = raw.strip().lstrip("/").lower().replace("_", "-")
    if key not in ROLE_ALIASES:
        known = ", ".join(sorted(set(ROLE_ALIASES.values())))
        raise SystemExit(f"unknown role '{raw}'. known: {known}")
    return ROLE_ALIASES[key]


def list_inbox_for(role: str) -> list[tuple[Path, dict, str]]:
    """Return (path, meta, body) for every open message addressed to role,
    oldest first by filename (filename starts with timestamp)."""
    if not MESSAGES_INBOX.is_dir():
        return []
    out: list[tuple[Path, dict, str]] = []
    for p in sorted(MESSAGES_INBOX.iterdir()):
        if p.suffix != ".md" or p.name.startswith("."):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = parse_frontmatter(text)
        if not meta:
            continue
        if meta.get("to") != role:
            continue
        if meta.get("status") != "open":
            continue
        if meta.get("type") == "reply":
            # never claim a reply, only requests/escalations/handoffs
            continue
        out.append((p, meta, body))
    return out


def claim_message(path: Path, meta: dict, body: str) -> bool:
    """Flip status open -> claimed via atomic write. Returns True on success."""
    if meta.get("status") != "open":
        return False
    meta["status"] = "claimed"
    new_text = serialize_frontmatter(meta, body)
    atomic_write_text(path, new_text)
    return True


def mark_answered(path: Path) -> bool:
    """Re-read message, flip claimed -> answered. Returns True on success."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    meta, body = parse_frontmatter(text)
    if meta.get("status") not in {"claimed", "open"}:
        return False
    meta["status"] = "answered"
    atomic_write_text(path, serialize_frontmatter(meta, body))
    return True


def write_reply(role: str, original_meta: dict, reply_text: str,
                *, inbox: Path | None = None) -> Path:
    """Create a reply message file in the given inbox (default agents/messages/inbox/).

    `inbox` lets a caller target the same inbox a message came from (e.g. a
    test tmp dir or auto_dispatch's snapshot source) without coupling to the
    module global. Defaults to MESSAGES_INBOX so existing callers are unchanged.
    """
    dest = inbox if inbox is not None else MESSAGES_INBOX
    dest.mkdir(parents=True, exist_ok=True)
    msg_id = f"MSG-{ts_compact()}-{uuid.uuid4().hex[:6]}"
    target = dest / f"{msg_id}.md"
    meta = {
        "id": msg_id,
        "from": role,
        "to": original_meta.get("from", "orchestrator"),
        "task_id": original_meta.get("task_id", "none"),
        "intent": f"reply to {original_meta.get('id', 'unknown')}",
        "type": "reply",
        "status": "open",
        "ts": ts_now_iso(),
        "in_reply_to": original_meta.get("id", ""),
        "evidence": [],
        "next": [],
    }
    body = reply_text.rstrip("\n") + "\n"
    target.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
    return target


def log(cfg: WorkerConfig, msg: str) -> None:
    if cfg.verbose:
        print(f"[{cfg.role}] {msg}", flush=True)


def process_one(cfg: WorkerConfig, provider: Provider) -> bool:
    """Process at most one message. Return True if one was processed."""
    candidates = list_inbox_for(cfg.role)
    candidates = [(p, m, b) for (p, m, b) in candidates
                  if m.get("id") not in cfg.handled_ids]
    if not candidates:
        return False

    path, meta, body = candidates[0]
    msg_id = meta.get("id", path.stem)
    cfg.handled_ids.add(msg_id)

    log(cfg, f"claiming {path.name}")
    if not claim_message(path, meta, body):
        log(cfg, f"claim failed (concurrent change?), skipping {path.name}")
        return False
    append_event(EVENTS_DIR, cfg.role, "message_claimed",
                 message_id=msg_id, path=str(path.relative_to(REPO_ROOT)))

    instruction = body if body.strip() else str(meta.get("intent", ""))
    routing_decision = _message_routing_decision(cfg, meta, instruction)
    _apply_routing_to_provider(provider, cfg.provider_name, routing_decision)
    context = {
        "original_msg_id": msg_id,
        "task_id": _metadata_task_id(meta),
        "from": meta.get("from", "unknown"),
        "intent": meta.get("intent", ""),
        "working_dir": str(REPO_ROOT),
        "provider_model": getattr(provider, "model", None),
        "routing": routing_decision,
    }
    log(cfg, f"provider={provider.name} run()")
    # TASK-101: provider.run returns ProviderResult (was plain str).
    # TASK-102: a ProviderError must not kill the worker — record it and reply
    # with the error so the loop keeps serving other messages.
    result = None
    try:
        result = provider.run(cfg.role, instruction, context)
    except NotImplementedError as exc:
        log(cfg, f"provider unsupported ({type(exc).__name__}): {exc}")
        eval_recorded, eval_skip_reason = _record_eval_outcome(
            cfg, meta, provider, routing_decision,
            tokens=0, finish_reason="error", error=str(exc), actual_tokens_known=False,
        )
        event_fields = {
            "provider": provider.name,
            "message_id": msg_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "eval_recorded": eval_recorded,
        }
        if eval_skip_reason:
            event_fields["eval_skip_reason"] = eval_skip_reason
        append_event(EVENTS_DIR, cfg.role, "provider_error", **event_fields)
        reply_text = f"[{cfg.role}/{provider.name}] provider unsupported ({type(exc).__name__}): {exc}"
    except ProviderError as exc:
        log(cfg, f"provider error ({type(exc).__name__}): {exc}")
        eval_recorded, eval_skip_reason = _record_eval_outcome(
            cfg, meta, provider, routing_decision,
            tokens=0, finish_reason="error", error=str(exc), actual_tokens_known=False,
        )
        event_fields = {
            "provider": provider.name,
            "message_id": msg_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "eval_recorded": eval_recorded,
        }
        if eval_skip_reason:
            event_fields["eval_skip_reason"] = eval_skip_reason
        append_event(EVENTS_DIR, cfg.role, "provider_error", **event_fields)
        reply_text = f"[{cfg.role}/{provider.name}] provider error ({type(exc).__name__}): {exc}"
    else:
        reply_text = result.text
        eval_recorded, eval_skip_reason = _record_eval_outcome(
            cfg, meta, provider, routing_decision, result=result,
        )
        event_fields = {
            "provider": provider.name,
            "message_id": msg_id,
            "model": getattr(provider, "model", None),
            **routing_event_fields(routing_decision),
            "eval_recorded": eval_recorded,
            "reply_chars": len(reply_text),
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "finish_reason": result.finish_reason,
        }
        if eval_skip_reason:
            event_fields["eval_skip_reason"] = eval_skip_reason
        append_event(EVENTS_DIR, cfg.role, "provider_called", **event_fields)

    reply_path = write_reply(cfg.role, meta, reply_text)
    log(cfg, f"wrote reply {reply_path.name}")
    append_event(EVENTS_DIR, cfg.role, "reply_written",
                 reply_id=reply_path.stem, in_reply_to=msg_id,
                 path=str(reply_path.relative_to(REPO_ROOT)))

    if mark_answered(path):
        log(cfg, f"marked original {msg_id} answered")
        append_event(EVENTS_DIR, cfg.role, "status_updated",
                     message_id=msg_id, from_status="claimed",
                     to_status="answered")
    else:
        log(cfg, f"WARN failed to mark {msg_id} answered")

    # TASK-111: if this message is part of a pipeline and the provider succeeded,
    # emit the next stage (or a terminal notice). Decentralized handoff (B2).
    if result is not None:
        try:
            import pipeline
            nxt = pipeline.compute_next(meta, reply_text, list(result.changed_files))
            if nxt is not None:
                np = pipeline.write_stage_message(nxt, MESSAGES_INBOX)
                log(cfg, f"pipeline → {nxt.kind} to {nxt.to} (stage {nxt.stage})")
                append_event(EVENTS_DIR, cfg.role, "pipeline_advanced",
                             pipeline=nxt.pipeline, kind=nxt.kind, to=nxt.to,
                             to_stage=nxt.stage,
                             path=str(np.relative_to(REPO_ROOT)))
        except Exception as exc:  # never let pipeline logic kill the worker
            log(cfg, f"pipeline advance skipped: {exc}")

    return True


def start_inbox_watcher(signal: threading.Event, log_fn=None):
    """TASK-105: wake the worker loop on inbox filesystem changes.

    Thin wrapper over the shared fs_watch helper (TASK-107) bound to the inbox
    directory. Returns the Observer, or None when watchdog is unavailable (the
    loop then falls back to plain polling).
    """
    from fs_watch import start_fs_watcher
    return start_fs_watcher([MESSAGES_INBOX], signal, log_fn)


def run_loop(cfg: WorkerConfig) -> int:
    provider = get_provider(cfg.provider_name)
    log(cfg, f"worker started — provider={provider.name} poll={cfg.poll_interval}s "
             f"timeout={cfg.timeout or 'infinite'} stop_file={cfg.stop_file} "
             f"watch_fs={cfg.watch_fs}")

    # TASK-105: optional event-driven wakeups. Polling stays as the fallback so
    # correctness never depends on event delivery — every wakeup re-scans inbox.
    inbox_signal = threading.Event()
    observer = None
    wait_mode = "poll"
    if cfg.watch_fs:
        observer = start_inbox_watcher(inbox_signal, lambda m: log(cfg, m))
        wait_mode = "watch_fs" if observer is not None else "poll(fallback)"

    append_event(EVENTS_DIR, cfg.role, "worker_started",
                 provider=provider.name,
                 poll_interval=cfg.poll_interval,
                 timeout=cfg.timeout,
                 stop_file=str(cfg.stop_file) if cfg.stop_file else None,
                 once=cfg.once,
                 wait_mode=wait_mode)

    started = time.time()
    deadline = (started + cfg.timeout) if cfg.timeout > 0 else None
    reason = "interrupted"
    try:
        while True:
            if cfg.stop_file and cfg.stop_file.exists():
                log(cfg, f"stop-file detected: {cfg.stop_file}")
                reason = "stop_file"
                break
            if deadline is not None and time.time() >= deadline:
                log(cfg, "timeout reached")
                reason = "timeout"
                break
            processed = process_one(cfg, provider)
            if processed and cfg.once:
                reason = "once"
                break
            if not processed:
                if observer is not None:
                    # wake on inbox change OR poll_interval (fallback safety net)
                    inbox_signal.wait(timeout=cfg.poll_interval)
                    inbox_signal.clear()
                else:
                    time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        log(cfg, "KeyboardInterrupt — shutting down")
        reason = "keyboard_interrupt"
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)

    append_event(EVENTS_DIR, cfg.role, "worker_stopped",
                 reason=reason,
                 ran_seconds=round(time.time() - started, 3),
                 handled=len(cfg.handled_ids))
    log(cfg, f"worker stopped — reason={reason} handled={len(cfg.handled_ids)}")
    return 0


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_worker",
        description="Agent Worker Runtime Loop (TASK-099).",
    )
    parser.add_argument("--role", required=True,
                        help=f"role to represent. one of: {sorted(set(ROLE_ALIASES.values()))}")
    parser.add_argument("--provider", default="dummy",
                        help="provider name (dummy is the only one TASK-099 ships)")
    parser.add_argument("--poll-interval", type=float, default=1.0,
                        help="seconds between inbox polls when idle (default 1.0)")
    parser.add_argument("--timeout", type=float, default=0.0,
                        help="exit after this many seconds (0 = infinite, default)")
    parser.add_argument("--stop-file", default=None,
                        help="exit immediately if this file exists; "
                             "default agents/runtime/stop/<role>.stop")
    parser.add_argument("--once", action="store_true",
                        help="process one message and exit (for tests/demos)")
    parser.add_argument("--watch-fs", action="store_true",
                        help="TASK-105: 인박스 변경을 watchdog 이벤트로 감지해 즉시 반응 "
                             "(opt-in, watchdog 필요). 미설치/미지정 시 폴링 fallback")
    parser.add_argument("--model",
                        help="provider model tier: auto, haiku, sonnet, opus, or provider model name")
    parser.add_argument("--routing-grade", default="Medium",
                        help="task grade used when --model=auto (default Medium)")
    parser.add_argument("--routing-prompt", default="",
                        help="prompt text used when --model=auto")
    parser.add_argument("--routing-changed-file", action="append",
                        help="changed file path used by routing (repeatable)")
    parser.add_argument("--routing-diff-lines", type=int, default=0,
                        help="approximate changed line count used by routing")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-step stdout (events still recorded)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    role = normalize_role(args.role)
    routing_decision = apply_model_routing_env(
        args.provider,
        model=args.model,
        grade=args.routing_grade,
        prompt=args.routing_prompt or args.role,
        changed_files=args.routing_changed_file,
        diff_lines=args.routing_diff_lines,
    )
    stop_file = Path(args.stop_file) if args.stop_file else STOP_DIR / f"{role}.stop"
    cfg = WorkerConfig(
        role=role,
        provider_name=args.provider,
        poll_interval=max(args.poll_interval, 0.05),
        timeout=max(args.timeout, 0.0),
        stop_file=stop_file,
        once=args.once,
        verbose=not args.quiet,
        watch_fs=args.watch_fs,
        routing_decision=routing_decision,
        routing_model=args.model,
        routing_grade=args.routing_grade,
        routing_prompt=args.routing_prompt,
        routing_changed_files=args.routing_changed_file,
        routing_diff_lines=args.routing_diff_lines,
    )
    if routing_decision and not args.quiet:
        print(
            "[agent_worker] model routing "
            f"selected={routing_decision['selected_tier']} "
            f"policy={routing_decision['policy_tier']} "
            f"signals={','.join(routing_decision['signals']) or '-'}",
            flush=True,
        )
    return run_loop(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
