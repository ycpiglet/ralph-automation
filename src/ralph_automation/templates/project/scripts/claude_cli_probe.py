#!/usr/bin/env python3
"""Probe Claude Code CLI and scheduler readiness without spending model usage.

Default mode is read-only and non-billable: it checks executable presence,
version output, terminal split prerequisites, and schedule registry state. It
does NOT run `claude -p` unless `--live-smoke` is explicitly passed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_LIVE_PROMPT = "Reply with exactly OK."


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(args: list[str], timeout: float) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(124, exc.stdout or "", exc.stderr or f"timeout after {timeout}s")
    except Exception as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _version(claude_path: str | None, run: Callable[[list[str], float], CommandResult],
             timeout: float) -> dict:
    if not claude_path:
        return {"present": False, "path": None, "version": None, "error": "claude not found on PATH"}
    result = run([claude_path, "--version"], timeout)
    version = result.stdout.strip() if result.returncode == 0 else None
    return {
        "present": True,
        "path": claude_path,
        "version": version,
        "error": None if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip()),
    }


def _live_smoke(claude_path: str | None, run: Callable[[list[str], float], CommandResult],
                prompt: str, timeout: float) -> dict:
    if not claude_path:
        return {"status": "failed", "reason": "claude not found on PATH", "command": []}
    command = [
        claude_path,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--max-turns",
        "1",
    ]
    result = run(command, timeout)
    payload = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except Exception:
            payload = None
    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "reason": None if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip()),
        "command": command,
        "stdout_chars": len(result.stdout or ""),
        "json_result_present": isinstance(payload, dict) and "result" in payload,
    }


def _default_os_task() -> dict:
    try:
        import schedule_task
        return schedule_task.query_os_task(details=False)
    except Exception as exc:
        return {"available": False, "registered": False, "reason": str(exc)}


def _default_schedules() -> list[dict]:
    try:
        import schedule as schedule_mod
        return schedule_mod.read_schedules()
    except Exception:
        return []


def _recommendations(result: dict) -> list[str]:
    recs: list[str] = []
    if not result["scheduler"].get("os_registered") and result["scheduler"].get("enabled_schedules", 0):
        recs.append("Register OS trigger with `python scripts/schedule_task.py register` only when unattended scheduled notification is intended.")
    if not result["terminal"].get("can_split"):
        recs.append("Do not rely on interactive pane automation; use scheduler + agent_worker, and keep panes as observers/manual review rooms.")
    if result["live_smoke"]["status"] == "skipped" and result["claude"]["present"]:
        recs.append("Optional next fact: run `python scripts/claude_cli_probe.py --live-smoke --json` to test the Claude App CLI path; this may consume Claude Code usage.")
    if result["live_smoke"]["status"] == "ok":
        recs.append("Claude CLI can be considered as a review-only scheduled sidecar; keep `claude-agent` API worker gated by TASK-221.")
    recs.append("Keep Codex/agent_worker as the primary automation path until Claude CLI live-smoke is proven stable.")
    return recs


def build_probe(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[list[str], float], CommandResult] = run_command,
    os_task: dict | None = None,
    schedules: list[dict] | None = None,
    include_live_smoke: bool = False,
    live_prompt: str = DEFAULT_LIVE_PROMPT,
    timeout: float = 12.0,
) -> dict:
    claude_path = which("claude")
    codex_path = which("codex")
    wt_path = which("wt")
    shell_path = which("pwsh") or which("powershell")
    schedule_state = os_task if os_task is not None else _default_os_task()
    schedule_rows = schedules if schedules is not None else _default_schedules()
    enabled_schedules = len([row for row in schedule_rows if row.get("enabled")])

    result = {
        "claude": _version(claude_path, run, timeout),
        "codex": {
            "present": bool(codex_path),
            "path": codex_path,
        },
        "terminal": {
            "wt": wt_path,
            "shell": shell_path,
            "can_split": bool(wt_path and shell_path),
        },
        "scheduler": {
            "os_available": bool(schedule_state.get("available", True)),
            "os_registered": bool(schedule_state.get("registered")),
            "enabled_schedules": enabled_schedules,
        },
        "live_smoke": (
            _live_smoke(claude_path, run, live_prompt, timeout)
            if include_live_smoke
            else {
                "status": "skipped",
                "reason": "pass --live-smoke to run `claude -p`; default probe never spends model usage",
                "command": [],
            }
        ),
    }
    result["recommendations"] = _recommendations(result)
    return result


def render(result: dict) -> str:
    lines = [
        "# Claude CLI Probe",
        "",
        f"- claude: {'present' if result['claude']['present'] else 'missing'}"
        + (f" ({result['claude'].get('version')})" if result["claude"].get("version") else ""),
        f"- codex CLI: {'present' if result['codex']['present'] else 'missing'}",
        f"- terminal split: {'ready' if result['terminal']['can_split'] else 'not ready'}",
        f"- scheduler: OS registered={result['scheduler']['os_registered']}, enabled={result['scheduler']['enabled_schedules']}",
        f"- live smoke: {result['live_smoke']['status']}",
        "",
        "## Recommendations",
    ]
    lines.extend(f"- {item}" for item in result["recommendations"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Claude CLI automation readiness.")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live-smoke", action="store_true", help="run `claude -p` non-interactive smoke; may consume Claude Code usage")
    parser.add_argument("--prompt", default=DEFAULT_LIVE_PROMPT, help="prompt for --live-smoke")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = build_probe(include_live_smoke=args.live_smoke, live_prompt=args.prompt, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result), end="")
    return 0 if result["live_smoke"]["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
