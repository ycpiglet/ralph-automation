#!/usr/bin/env python3
"""Codex session subagent bridge (TASK-135).

This module does not pretend that repository Python can call Codex platform
developer tools. Instead, it creates an auditable packet for the *parent Codex
session* to execute with `multi_agent_v1.spawn_agent`, then records the result
back into the existing message bus.

Workflow:
  1. dispatch       -> render prompt + optional subagent_call + packet JSON
  2. parent Codex   -> spawn/wait Codex subagent with packet["prompt"]
  3. record-reply   -> write subagent_reply + mark the call answered

Council helpers mirror the same pattern for 2-3 member Codex subagent councils.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subagent_council as sc  # noqa: E402
import subagent_dispatch as sd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT / "agents" / "runtime" / "codex_subagents"
SCHEMA_VERSION = 1


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    now = _now()
    return f"{prefix}-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _display(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _packet_path(bridge_id: str) -> Path:
    return BRIDGE_DIR / f"{bridge_id}.json"


def _write_packet(packet: dict, dry_run: bool) -> Path:
    path = _packet_path(packet["id"])
    packet["packet_path"] = _display(path)
    if not dry_run:
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return path


def _load_packet(bridge_id: str) -> dict:
    path = _packet_path(bridge_id)
    if not path.is_file():
        raise FileNotFoundError(f"missing Codex subagent packet: {_display(path)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _update_packet(bridge_id: str, updates: dict, dry_run: bool) -> dict:
    try:
        packet = _load_packet(bridge_id)
    except FileNotFoundError:
        packet = {"id": bridge_id, "schema_version": SCHEMA_VERSION}
    packet.update(updates)
    if not dry_run:
        _write_packet(packet, dry_run=False)
    return packet


def _suggested_agent_type(role_id: str) -> str:
    return "worker" if role_id == "implementer" else "explorer"


def _execution_instructions(role_id: str) -> dict:
    return {
        "tool": "multi_agent_v1.spawn_agent",
        "suggested_agent_type": _suggested_agent_type(role_id),
        "parent_session_only": True,
        "after_completion": (
            "Run `python scripts/codex_subagent_bridge.py record-reply "
            "--bridge-id <id> --verdict <APPROVED|NEEDS_CHANGES|...> "
            "--summary-file <file>` from the parent Codex session."
        ),
    }


def _mark_message_answered(parent_id: str, dry_run: bool) -> bool:
    """Best-effort status transition for the matching subagent_call message."""
    path = sd.MESSAGES_INBOX / f"{parent_id}.md"
    if dry_run or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if "status: open" in text:
        path.write_text(text.replace("status: open", "status: answered", 1),
                        encoding="utf-8")
        return True
    if "status: claimed" in text:
        path.write_text(text.replace("status: claimed", "status: answered", 1),
                        encoding="utf-8")
        return True
    return False


def _read_summary(summary: str | None, summary_file: str | None) -> str:
    if summary_file:
        return Path(summary_file).read_text(encoding="utf-8").strip()
    return (summary or "").strip()


def _parse_members(raw: str) -> list[str]:
    members = [m.strip() for m in raw.split(",") if m.strip()]
    if len(members) < 2:
        raise ValueError("a council needs at least two members")
    return members


def _parse_verdicts(items: list[str]) -> list[sc.Verdict]:
    verdicts: list[sc.Verdict] = []
    for raw in items or []:
        if "=" not in raw:
            raise ValueError(f"--verdict must be role=vote[:summary], got {raw!r}")
        role, _, rest = raw.partition("=")
        vote, _, summary = rest.partition(":")
        verdicts.append(sc.Verdict(role=role.strip(), vote=vote.strip(),
                                   summary=summary.strip()))
    return verdicts


def create_dispatch_packet(
    *,
    role_id: str,
    task_id: str,
    intent: str,
    context_packet_path: str | None = None,
    sender: str = "lead-engineer",
    evidence: list[str] | None = None,
    emit_call: bool = False,
    dry_run: bool = False,
) -> dict:
    """Create a single Codex subagent dispatch packet."""
    sd.get_role(role_id)
    bridge_id = _new_id("CODEX-SUBAGENT")
    prompt = sd.render_prompt(
        role_id=role_id,
        task_id=task_id,
        intent=intent,
        context_packet_path=context_packet_path,
        extra_context=(
            "Codex runtime note: this is a session-layer subagent dispatch. "
            "The parent Codex session will call `multi_agent_v1.spawn_agent`; "
            "repository Python only records the packet and evidence."
        ),
    )
    packet_path = _packet_path(bridge_id)
    ev = list(evidence or [])
    ev.append(_display(packet_path) or str(packet_path))

    call_path = None
    event_path = None
    if emit_call:
        call_path = sd.emit_call_message(
            role_id=role_id,
            task_id=task_id,
            intent=intent,
            sender=sender,
            evidence=ev,
            dry_run=dry_run,
        )
        event_path = sd.emit_event(
            role_id=role_id,
            task_id=task_id,
            kind="dispatch",
            extra={
                "runtime": "codex-session",
                "bridge_id": bridge_id,
                "message_id": call_path.stem,
                "intent": intent,
            },
            dry_run=dry_run,
        )

    packet = {
        "id": bridge_id,
        "schema_version": SCHEMA_VERSION,
        "kind": "codex_session_subagent_dispatch",
        "runtime": "codex-session",
        "status": "pending_parent_spawn",
        "created_at": _now_iso(),
        "sender": sender,
        "task_id": task_id,
        "role": role_id,
        "intent": intent,
        "context_packet": context_packet_path,
        "evidence": ev,
        "prompt": prompt,
        "call_message": _display(call_path),
        "dispatch_event": _display(event_path),
        "execution": _execution_instructions(role_id),
    }
    _write_packet(packet, dry_run=dry_run)
    return packet


def record_reply(
    *,
    bridge_id: str,
    verdict: str,
    role_id: str | None = None,
    task_id: str | None = None,
    parent_id: str | None = None,
    sender: str = "lead-engineer",
    summary: str | None = None,
    summary_file: str | None = None,
    evidence: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Record a completed Codex subagent result as subagent_reply evidence."""
    packet = _load_packet(bridge_id)
    role = role_id or packet.get("role")
    task = task_id or packet.get("task_id")
    if not role or not task:
        raise ValueError("role/task_id required when packet does not provide them")
    sd.get_role(role)

    call_message = packet.get("call_message")
    parent = parent_id
    if not parent and call_message:
        parent = Path(str(call_message)).stem
    if not parent:
        raise ValueError("--parent-id is required when the packet has no call_message")

    ev = list(evidence or [])
    packet_path = packet.get("packet_path")
    if packet_path:
        ev.append(packet_path)
    text = _read_summary(summary, summary_file)
    reply_path = sd.emit_reply_message(
        parent_id=parent,
        role_id=role,
        task_id=task,
        verdict=verdict,
        sender=sender,
        summary=text,
        evidence=ev,
        dry_run=dry_run,
    )
    event_path = sd.emit_event(
        role_id=role,
        task_id=task,
        kind="reply",
        extra={
            "runtime": "codex-session",
            "bridge_id": bridge_id,
            "message_id": reply_path.stem,
            "in_reply_to": parent,
            "verdict": verdict,
        },
        dry_run=dry_run,
    )
    marked = _mark_message_answered(parent, dry_run=dry_run)
    updates = {
        "status": "completed",
        "completed_at": _now_iso(),
        "verdict": verdict,
        "reply_message": _display(reply_path),
        "reply_event": _display(event_path),
        "parent_marked_answered": marked,
    }
    _update_packet(bridge_id, updates, dry_run=dry_run)
    return {**updates, "role": role, "task_id": task, "summary": text}


def create_council_packet(
    *,
    task_id: str,
    members: list[str],
    intent: str,
    method: str = "any_veto",
    context_packet_path: str | None = None,
    sender: str = "lead-engineer",
    evidence: list[str] | None = None,
    emit_calls: bool = False,
    dry_run: bool = False,
) -> dict:
    """Create a Codex subagent council packet with one prompt per member."""
    if method not in sc.CONSENSUS_METHODS:
        raise ValueError(f"method must be one of {sorted(sc.CONSENSUS_METHODS)}")
    prompts = sc.render_council_prompts(
        task_id=task_id,
        members=members,
        intent=intent,
        context_packet_path=context_packet_path,
    )
    bridge_id = _new_id("CODEX-COUNCIL")
    packet_path = _packet_path(bridge_id)
    ev = list(evidence or [])
    ev.append(_display(packet_path) or str(packet_path))

    calls: list[dict] = []
    if emit_calls:
        for member in members:
            call_path = sd.emit_call_message(
                role_id=member,
                task_id=task_id,
                intent=f"{intent} (council member: {member})",
                sender=sender,
                evidence=ev,
                dry_run=dry_run,
            )
            event_path = sd.emit_event(
                role_id=member,
                task_id=task_id,
                kind="dispatch",
                extra={
                    "runtime": "codex-session",
                    "bridge_id": bridge_id,
                    "council_member": member,
                    "message_id": call_path.stem,
                    "intent": intent,
                },
                dry_run=dry_run,
            )
            calls.append({
                "role": member,
                "call_message": _display(call_path),
                "dispatch_event": _display(event_path),
            })

    packet = {
        "id": bridge_id,
        "schema_version": SCHEMA_VERSION,
        "kind": "codex_session_subagent_council",
        "runtime": "codex-session",
        "status": "pending_parent_spawn",
        "created_at": _now_iso(),
        "sender": sender,
        "task_id": task_id,
        "members": members,
        "method": method,
        "intent": intent,
        "context_packet": context_packet_path,
        "evidence": ev,
        "prompts": prompts,
        "call_messages": calls,
        "execution": {
            "tool": "multi_agent_v1.spawn_agent",
            "parent_session_only": True,
            "suggested_parallelism": "spawn one Codex subagent per member",
            "after_completion": (
                "Run `python scripts/codex_subagent_bridge.py council-record "
                "--bridge-id <id> --task-id <task> --method <method> "
                "--verdict role=vote[:summary] ...`."
            ),
        },
    }
    _write_packet(packet, dry_run=dry_run)
    return packet


def record_council(
    *,
    bridge_id: str,
    task_id: str | None,
    method: str | None,
    verdicts: list[sc.Verdict],
    sender: str = "lead-engineer",
    dry_run: bool = False,
) -> dict:
    packet = _load_packet(bridge_id)
    task = task_id or packet.get("task_id")
    method_name = method or packet.get("method")
    if not task or not method_name:
        raise ValueError("task_id/method required when packet does not provide them")
    result = sc.decide(method_name, verdicts)
    consensus_path = sc.emit_consensus_message(
        task_id=task,
        result=result,
        sender=sender,
        dry_run=dry_run,
    )
    event_path = sd.emit_event(
        role_id="council",
        task_id=task,
        kind="verdict",
        extra={
            "runtime": "codex-session",
            "bridge_id": bridge_id,
            "method": result.method,
            "final": result.final,
            "message_id": consensus_path.stem,
        },
        dry_run=dry_run,
    )
    marked_calls: list[str] = []
    for call in packet.get("call_messages") or []:
        call_path = call.get("call_message")
        if not call_path:
            continue
        parent_id = Path(str(call_path)).stem
        if _mark_message_answered(parent_id, dry_run=dry_run):
            marked_calls.append(parent_id)
    updates = {
        "status": "completed",
        "completed_at": _now_iso(),
        "final": result.final,
        "rationale": result.rationale,
        "consensus_message": _display(consensus_path),
        "verdict_event": _display(event_path),
        "parent_calls_marked_answered": marked_calls,
    }
    _update_packet(bridge_id, updates, dry_run=dry_run)
    return {**updates, "task_id": task, "method": result.method}


def _print_packet(packet: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return
    print(f"id: {packet['id']}")
    print(f"kind: {packet['kind']}")
    print(f"packet: {packet.get('packet_path')}")
    if packet.get("call_message"):
        print(f"call_message: {packet['call_message']}")
    if packet.get("call_messages"):
        print("call_messages:")
        for call in packet["call_messages"]:
            print(f"  - {call['role']}: {call['call_message']}")
    print("execution: parent Codex session must call multi_agent_v1.spawn_agent")
    if "prompt" in packet:
        print("\n--- prompt ---")
        print(packet["prompt"])
    elif "prompts" in packet:
        for role, prompt in packet["prompts"].items():
            print(f"\n--- prompt: {role} ---")
            print(prompt)


def _cmd_dispatch(args: argparse.Namespace) -> int:
    packet = create_dispatch_packet(
        role_id=args.role,
        task_id=args.task_id,
        intent=args.intent,
        context_packet_path=args.context_packet,
        sender=args.sender,
        evidence=args.evidence or [],
        emit_call=args.emit_call,
        dry_run=args.dry_run,
    )
    _print_packet(packet, args.json)
    return 0


def _cmd_record_reply(args: argparse.Namespace) -> int:
    try:
        result = record_reply(
            bridge_id=args.bridge_id,
            role_id=args.role,
            task_id=args.task_id,
            parent_id=args.parent_id,
            verdict=args.verdict,
            sender=args.sender,
            summary=args.summary,
            summary_file=args.summary_file,
            evidence=args.evidence or [],
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_council_plan(args: argparse.Namespace) -> int:
    try:
        members = _parse_members(args.members)
        packet = create_council_packet(
            task_id=args.task_id,
            members=members,
            intent=args.intent,
            method=args.method,
            context_packet_path=args.context_packet,
            sender=args.sender,
            evidence=args.evidence or [],
            emit_calls=args.emit_calls,
            dry_run=args.dry_run,
        )
    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    _print_packet(packet, args.json)
    return 0


def _cmd_council_record(args: argparse.Namespace) -> int:
    try:
        verdicts = _parse_verdicts(args.verdict)
        result = record_council(
            bridge_id=args.bridge_id,
            task_id=args.task_id,
            method=args.method,
            verdicts=verdicts,
            sender=args.sender,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["final"] == "approved" else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex_subagent_bridge.py",
        description="Codex session subagent bridge (TASK-135).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dispatch", help="create one Codex subagent packet")
    d.add_argument("--role", required=True, choices=sd.list_roles())
    d.add_argument("--task-id", required=True)
    d.add_argument("--intent", required=True)
    d.add_argument("--context-packet")
    d.add_argument("--sender", default="lead-engineer")
    d.add_argument("--evidence", action="append")
    d.add_argument("--emit-call", action="store_true")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_dispatch)

    rr = sub.add_parser("record-reply", help="record a Codex subagent result")
    rr.add_argument("--bridge-id", required=True)
    rr.add_argument("--role", choices=sd.list_roles())
    rr.add_argument("--task-id")
    rr.add_argument("--parent-id")
    rr.add_argument("--verdict", required=True)
    rr.add_argument("--summary")
    rr.add_argument("--summary-file")
    rr.add_argument("--sender", default="lead-engineer")
    rr.add_argument("--evidence", action="append")
    rr.add_argument("--dry-run", action="store_true")
    rr.set_defaults(func=_cmd_record_reply)

    cp = sub.add_parser("council-plan", help="create a Codex council packet")
    cp.add_argument("--task-id", required=True)
    cp.add_argument("--members", required=True)
    cp.add_argument("--intent", required=True)
    cp.add_argument("--method", default="any_veto",
                    choices=sorted(sc.CONSENSUS_METHODS))
    cp.add_argument("--context-packet")
    cp.add_argument("--sender", default="lead-engineer")
    cp.add_argument("--evidence", action="append")
    cp.add_argument("--emit-calls", action="store_true")
    cp.add_argument("--dry-run", action="store_true")
    cp.add_argument("--json", action="store_true")
    cp.set_defaults(func=_cmd_council_plan)

    cr = sub.add_parser("council-record", help="record a Codex council result")
    cr.add_argument("--bridge-id", required=True)
    cr.add_argument("--task-id")
    cr.add_argument("--method", choices=sorted(sc.CONSENSUS_METHODS))
    cr.add_argument("--verdict", action="append", required=True,
                    help="role=vote[:summary], repeatable")
    cr.add_argument("--sender", default="lead-engineer")
    cr.add_argument("--dry-run", action="store_true")
    cr.set_defaults(func=_cmd_council_record)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
