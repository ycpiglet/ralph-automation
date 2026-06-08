import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("check_messages.py")
SPEC = importlib.util.spec_from_file_location("check_messages", MODULE_PATH)
check_messages = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["check_messages"] = check_messages
SPEC.loader.exec_module(check_messages)


def _write_message(base: Path, subdir: str, msg_id: str, status: str = "open") -> None:
    path = base / subdir / f"{msg_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"id: {msg_id}\n"
        "from: sample\n"
        "to: lead-engineer\n"
        "task_id: TASK-001\n"
        "intent: stale test\n"
        "type: request\n"
        f"status: {status}\n"
        "ts: 2026-05-22T09:12:07+09:00\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )


def test_sample_open_messages_do_not_emit_stale_warning(tmp_path, monkeypatch):
    base = tmp_path / "agents" / "messages"
    _write_message(base, "samples", "MSG-20260522-091207-a1b2c3")
    monkeypatch.setattr(check_messages, "MESSAGES_DIR", base)

    errors, warnings = check_messages.lint()

    assert errors == 0
    assert warnings == 0


def test_inbox_open_messages_still_emit_stale_warning(tmp_path, monkeypatch):
    base = tmp_path / "agents" / "messages"
    _write_message(base, "inbox", "MSG-20260522-091207-a1b2c3")
    monkeypatch.setattr(check_messages, "MESSAGES_DIR", base)

    errors, warnings = check_messages.lint()

    assert errors == 0
    assert warnings == 1
