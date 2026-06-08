#!/usr/bin/env python3
"""Bidirectional Q&A negotiation helper (TASK-119).

Standardizes the *question* / *answer* lifecycle on the message bus. Until
TASK-119 the bus carried `request`/`reply` (forward dispatch) and
`subagent_call`/`subagent_reply` (TASK-116 Stage 7-A). This module adds the
third pair — same model, different role mask, *formal* negotiation:

    type=question  + question_for: <role>     # asks
    type=answer    + in_reply_to: <q_msg_id>  # replies

The lifecycle is independent of `subagent_call` — questions can flow between
worker roles (backend ↔ qa) without any subagent. They can *also* be answered
by a subagent (TASK-116 integration); the schema does not require it.

CLI:
  python scripts/qa_negotiation.py ask \\
      --from backend --question-for qa --task-id TASK-119 --intent "..."
  python scripts/qa_negotiation.py answer \\
      --to MSG-... --from qa --task-id TASK-119 --intent "..." \\
      [--body-file path.md]
  python scripts/qa_negotiation.py list
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
MESSAGES_INBOX = ROOT / "agents" / "messages" / "inbox"

MSG_ID_RE = re.compile(r"^MSG-\d{8}-\d{6}-[0-9a-f]{6}$")


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _new_msg_id(when: _dt.datetime | None = None) -> str:
    w = when or _dt.datetime.now()
    return f"MSG-{w.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _format_frontmatter(fields: list[tuple[str, object]]) -> str:
    out = ["---"]
    for key, value in fields:
        if isinstance(value, list):
            if not value:
                out.append(f"{key}: []")
            else:
                out.append(f"{key}:")
                for item in value:
                    out.append(f"  - {item}")
        elif value is None or value == "":
            out.append(f"{key}:")
        else:
            out.append(f"{key}: {value}")
    out.append("---")
    return "\n".join(out)


def emit_question(
    from_role: str,
    question_for: str,
    task_id: str,
    intent: str,
    evidence: list[str] | None = None,
    body: str = "",
    dry_run: bool = False,
) -> Path:
    """Write a kind=question message to agents/messages/inbox/.

    `to` is set to `question_for` so the existing inbox routing (TASK-083)
    delivers the message without changes; `question_for` is duplicated as an
    explicit marker so future routers can fan-out without re-parsing intent.
    """
    if not from_role or not question_for:
        raise ValueError("from_role and question_for are required")
    if not task_id:
        raise ValueError("task_id is required (use 'none' for cross-task)")
    if not intent:
        raise ValueError("intent is required")
    msg_id = _new_msg_id()
    fields: list[tuple[str, object]] = [
        ("id", msg_id),
        ("from", from_role),
        ("to", question_for),
        ("task_id", task_id),
        ("intent", intent),
        ("type", "question"),
        ("status", "open"),
        ("ts", _now_iso()),
        ("in_reply_to", ""),
        ("question_for", question_for),
        ("evidence", evidence or []),
        ("next", []),
    ]
    text = _format_frontmatter(fields) + "\n\n" + (body or intent) + "\n"
    target = MESSAGES_INBOX / f"{msg_id}.md"
    if not dry_run:
        MESSAGES_INBOX.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return target


def emit_answer(
    question_msg_id: str,
    from_role: str,
    to_role: str,
    task_id: str,
    intent: str,
    body: str = "",
    evidence: list[str] | None = None,
    dry_run: bool = False,
) -> Path:
    """Write a kind=answer message replying to `question_msg_id`.

    `to_role` is the original asker (so the answer lands in their inbox view).
    Lifecycle: question status=open -> claimed (by answerer) -> answered after
    this message is written. The orchestrator performs the open->answered
    transition; this helper only writes the answer file.
    """
    if not MSG_ID_RE.match(question_msg_id):
        raise ValueError(
            f"question_msg_id must match MSG-YYYYMMDD-HHMMSS-{{6 hex}}, "
            f"got '{question_msg_id}'"
        )
    if not from_role or not to_role:
        raise ValueError("from_role and to_role are required")
    if not task_id:
        raise ValueError("task_id is required")
    if not intent:
        raise ValueError("intent is required")
    msg_id = _new_msg_id()
    fields: list[tuple[str, object]] = [
        ("id", msg_id),
        ("from", from_role),
        ("to", to_role),
        ("task_id", task_id),
        ("intent", intent),
        ("type", "answer"),
        ("status", "answered"),
        ("ts", _now_iso()),
        ("in_reply_to", question_msg_id),
        ("evidence", evidence or []),
        ("next", []),
    ]
    text = _format_frontmatter(fields) + "\n\n" + (body or intent) + "\n"
    target = MESSAGES_INBOX / f"{msg_id}.md"
    if not dry_run:
        MESSAGES_INBOX.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return target


def list_open_questions() -> list[Path]:
    """Return paths of inbox messages with type=question and status=open|claimed."""
    if not MESSAGES_INBOX.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(MESSAGES_INBOX.iterdir()):
        if p.suffix != ".md":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "type: question" not in text:
            continue
        if "status: open" not in text and "status: claimed" not in text:
            continue
        out.append(p)
    return out


# ---------- CLI ----------


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _cmd_ask(args: argparse.Namespace) -> int:
    try:
        path = emit_question(
            from_role=args.from_role,
            question_for=args.question_for,
            task_id=args.task_id,
            intent=args.intent,
            evidence=args.evidence or [],
            body=_read_body(args.body_file),
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    word = "would write" if args.dry_run else "wrote"
    print(f"[ask] {word} {_display(path)}")
    return 0


def _cmd_answer(args: argparse.Namespace) -> int:
    try:
        path = emit_answer(
            question_msg_id=args.to,
            from_role=args.from_role,
            to_role=args.to_role,
            task_id=args.task_id,
            intent=args.intent,
            body=_read_body(args.body_file),
            evidence=args.evidence or [],
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    word = "would write" if args.dry_run else "wrote"
    print(f"[answer] {word} {_display(path)}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for p in list_open_questions():
        print(_display(p))
    return 0


def _read_body(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qa_negotiation.py",
        description="Bidirectional Q&A negotiation helper (TASK-119).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("ask", help="emit a kind=question message")
    a.add_argument("--from", dest="from_role", required=True,
                   help="sender role (e.g. backend)")
    a.add_argument("--question-for", required=True,
                   help="role expected to answer (e.g. qa)")
    a.add_argument("--task-id", required=True, help="TASK-NNN or 'none'")
    a.add_argument("--intent", required=True, help="one-line question intent")
    a.add_argument("--body-file", help="optional path to a markdown body")
    a.add_argument("--evidence", action="append", help="evidence path (repeatable)")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=_cmd_ask)

    r = sub.add_parser("answer", help="emit a kind=answer message")
    r.add_argument("--to", required=True, dest="to",
                   help="question MSG-id being answered")
    r.add_argument("--from", dest="from_role", required=True,
                   help="answerer role (e.g. qa)")
    r.add_argument("--to-role", required=True,
                   help="recipient role (typically the asker)")
    r.add_argument("--task-id", required=True, help="TASK-NNN or 'none'")
    r.add_argument("--intent", required=True, help="one-line answer summary")
    r.add_argument("--body-file", help="optional path to a markdown body")
    r.add_argument("--evidence", action="append", help="evidence path (repeatable)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=_cmd_answer)

    l = sub.add_parser("list", help="list open/claimed questions in the inbox")
    l.set_defaults(func=_cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
