#!/usr/bin/env python3
"""Terminal/session adapter for the agent orchestrator (TASK-085).

This module keeps terminal launching separate from the command router:

  - `preview` prints the exact command sequence for an adapter.
  - `launch` requires `--yes` before opening a visible terminal.
  - `close-preview` explains terminal close vs orchestrator `/kill` semantics.

The default remains manual/preview-first. Real agent process execution is still
outside this repository; by default a pane opens on the role inbox command.
"""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = REPO_ROOT / "scripts" / "agent_orchestrator.py"
CONTEXT_PACKET_BUILDER = REPO_ROOT / "scripts" / "agent_context_packet.py"
AGENT_WORKER = REPO_ROOT / "scripts" / "agent_worker.py"
AGENT_OBSERVER = REPO_ROOT / "scripts" / "agent_observer.py"
CONTEXTS_DIR = REPO_ROOT / "agents" / "runtime" / "contexts"

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


@dataclass
class TerminalPlan:
    adapter: str
    available: bool
    execution_supported: bool
    detect_note: str
    title: str
    command: list[str]
    shell_command: str
    close_preview: list[str]

    def to_dict(self) -> dict:
        return {
            "adapter": self.adapter,
            "available": self.available,
            "execution_supported": self.execution_supported,
            "detect_note": self.detect_note,
            "title": self.title,
            "command": self.command,
            "shell_command": self.shell_command,
            "close_preview": self.close_preview,
        }


def normalize_role(raw: str) -> str:
    key = raw.strip().lstrip("/").lower().replace("_", "-")
    if key not in ROLE_ALIASES:
        known = ", ".join(sorted(set(ROLE_ALIASES.values())))
        raise SystemExit(f"unknown role '{raw}'. known: {known}")
    return ROLE_ALIASES[key]


def detect_os_family() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def which(binary: str) -> str | None:
    return shutil.which(binary)


def python_executable() -> str:
    return sys.executable or "python"


def default_agent_command(role: str, task: str) -> list[str]:
    # A real LLM runner is deliberately not assumed. The first pane lands on
    # the relevant inbox and task context, which is safe and deterministic.
    return [
        python_executable(),
        str(ORCHESTRATOR),
        "inbox",
        "--role",
        role,
        "--json",
    ]


def worker_agent_command(role: str, provider: str = "dummy") -> list[str]:
    # TASK-100: pane runs an autonomous agent_worker that polls inbox.
    # Long-running by design — pane stays alive showing live worker logs.
    return [
        python_executable(),
        str(AGENT_WORKER),
        "--role",
        role,
        "--provider",
        provider,
    ]


def observer_agent_command(role: str, exit_on_stop: bool = False) -> list[str]:
    # TASK-103: pane runs a read-only agent_observer that reconstructs the
    # agent's state from runtime files (event log + inbox). It does NOT host the
    # worker — the worker may run elsewhere. pane = camera view (AGENT_RUNTIME §7).
    # TASK-109: --exit-on-stop makes the observer (and, with --no-keep-open, the
    # pane) close once the watched work finishes.
    cmd = [python_executable(), str(AGENT_OBSERVER), "--role", role, "--watch"]
    if exit_on_stop:
        cmd.append("--exit-on-stop")
    return cmd


def build_context_packet(role: str, task: str) -> Path | None:
    """Invoke agent_context_packet.py and persist output under agents/runtime/contexts/.

    Returns the persisted path, or None if the builder is unavailable or fails.
    """
    if not CONTEXT_PACKET_BUILDER.exists():
        return None
    CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_task = (task or "none").replace("/", "_")
    out = CONTEXTS_DIR / f"{role}__{safe_task}.md"
    cmd = [python_executable(), str(CONTEXT_PACKET_BUILDER), "--role", role]
    if task and task != "none":
        cmd += ["--task", task]
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    out.write_text(result.stdout, encoding="utf-8")
    return out


def shell_kind_for(plan_shell: str | None) -> str:
    """Map shell path to a quoting/chaining kind."""
    if not plan_shell:
        return "posix"
    low = plan_shell.lower()
    if "pwsh" in low or "powershell" in low:
        return "powershell"
    return "posix"


def build_chained_command(
    *,
    inbox_parts: list[str],
    shell_kind: str,
    auto_start_claude: bool,
    context_packet_path: Path | None,
    working_dir: Path | None = None,
) -> str:
    """Build a single shell command string that:

    1. (Optional) cd into working_dir.
    2. Runs the inbox command (always).
    3. Optionally prints the context packet contents.
    4. Optionally launches `claude` with the first prompt pre-loaded.

    For PowerShell, uses `;` separator and `Get-Content`. For posix, uses `&&`
    and `cat`. The first-prompt-to-claude is passed via positional arg —
    `claude "<prompt>"` — which Claude Code interprets as the initial user
    message.

    TASK-098: working_dir 가 주어지면 명령 체인의 첫 단계로 cd / Set-Location 을
    추가. 이렇게 하면 wt 의 --starting-directory 같은 외부 옵션에 의존하지 않고
    PowerShell/bash 안에서 직접 디렉토리 설정 (wt 옵션 호환성 회피).
    """
    inbox_cmd = command_string(inbox_parts, shell=shell_kind)
    if shell_kind == "powershell":
        sep = "; "
        parts: list[str] = []
        if working_dir is not None:
            # PowerShell Set-Location, single-quote literal (백슬래시 경로 안전)
            parts.append(f"Set-Location '{working_dir}'")
        parts.append(inbox_cmd)
        if context_packet_path is not None:
            # TASK-097 fix: PowerShell 이 "..." 안의 (...) 를 subexpression 으로 평가해서
            # 'uiux__TASK-076.md: not recognized' 오류 발생 → single-quote 로 변경 (literal).
            parts.append(f"Write-Host '--- Context Packet ({context_packet_path.name}) ---'")
            parts.append(f'Get-Content "{context_packet_path}"')
            parts.append("Write-Host '--- End Context Packet ---'")
        if auto_start_claude:
            if context_packet_path is not None:
                parts.append(f'claude "@{context_packet_path}"')
            else:
                parts.append("claude")
        return sep.join(parts)
    # posix
    sep = " && "
    parts = []
    if working_dir is not None:
        parts.append(f"cd {shlex.quote(str(working_dir))}")
    parts.append(inbox_cmd)
    if context_packet_path is not None:
        parts.append(f'echo "--- Context Packet ({context_packet_path.name}) ---"')
        parts.append(f'cat {shlex.quote(str(context_packet_path))}')
        parts.append('echo "--- End Context Packet ---"')
    if auto_start_claude:
        if context_packet_path is not None:
            parts.append(f'claude "@{shlex.quote(str(context_packet_path))}"')
        else:
            parts.append("claude")
    return sep.join(parts)


def command_string(parts: list[str], *, shell: str) -> str:
    if shell == "powershell":
        quoted: list[str] = []
        for part in parts:
            if any(ch in part for ch in (" ", "\\", "/", ":", "(", ")")):
                quoted.append('"' + part.replace('"', '`"') + '"')
            else:
                quoted.append(part)
        return " ".join(quoted)
    return " ".join(shlex.quote(part) for part in parts)


def display_command(parts: list[str]) -> str:
    """Human-readable command preview with conservative quoting."""
    rendered: list[str] = []
    for part in parts:
        if part == ";":
            rendered.append(part)
        elif any(ch.isspace() for ch in part):
            rendered.append('"' + part.replace('"', '\\"') + '"')
        else:
            rendered.append(part)
    return " ".join(rendered)


def title_for(role: str, task: str, session_name: str) -> str:
    task_part = "" if task == "none" else f" {task}"
    return f"{session_name}:{role}{task_part}"


def build_manual_plan(role: str, task: str, session_name: str, raw_command: list[str]) -> TerminalPlan:
    title = title_for(role, task, session_name)
    shell_cmd = command_string(raw_command, shell="posix")
    close = [
        "Orchestrator session close: python scripts/agent_orchestrator.py kill <agent_id>",
        "Terminal close: close the pane/window manually after handoff is recorded.",
    ]
    return TerminalPlan("manual", True, False, "manual adapter -- preview only",
                        title, raw_command, shell_cmd, close)


def build_windows_terminal_plan(role: str, task: str, session_name: str,
                                raw_command: list[str], *,
                                split_mode: str = "tab",
                                auto_start_claude: bool = False,
                                context_packet_path: Path | None = None,
                                no_keep_open: bool = False) -> TerminalPlan:
    wt = which("wt")
    shell = which("pwsh") or which("powershell")
    title = title_for(role, task, session_name)
    # TASK-098 fix: wt 의 --starting-directory 옵션이 본 환경에서 split-pane 과 호환 안 됨
    # (옵션이 wt 에 의해 인식 안 되고 다음 토큰부터 PowerShell -Command 인자로 흘러
    # 0x80070005 액세스 거부 발생). PowerShell 안에서 Set-Location 으로 처리.
    shell_cmd = build_chained_command(
        inbox_parts=raw_command,
        shell_kind="powershell",
        auto_start_claude=auto_start_claude,
        context_packet_path=context_packet_path,
        working_dir=REPO_ROOT,
    )
    available = bool(wt and shell)
    cmd = []
    if available:
        # TASK-096 fix: Windows Terminal 은 ; 를 자기 명령 구분자로 해석.
        # PowerShell -Command 안의 ; 가 wt 에 의해 잘리지 않게 \; 로 escape.
        # wt 가 \; 를 ; 로 변환해 PowerShell 에 전달. (wt CLI 규약)
        wt_safe_cmd = shell_cmd.replace(";", "\\;")
        # TASK-109: --no-keep-open drops -NoExit so the pane closes when its
        # process exits (pairs with observer --exit-on-stop).
        shell_args = [shell, "-Command", wt_safe_cmd] if no_keep_open \
            else [shell, "-NoExit", "-Command", wt_safe_cmd]
        # split_mode 분기: tab (기본) / pane-vertical (옆으로) / pane-horizontal (아래로)
        if split_mode == "pane-vertical":
            cmd = [wt, "--window", "0", "split-pane", "-V", "--title", title, *shell_args]
        elif split_mode == "pane-horizontal":
            cmd = [wt, "--window", "0", "split-pane", "-H", "--title", title, *shell_args]
        else:  # tab (default)
            cmd = [wt, "--window", "0", "new-tab", "--title", title, *shell_args]
    close = [
        "Orchestrator session close: python scripts/agent_orchestrator.py kill <agent_id>",
        "Windows Terminal close: close the tab/pane manually, or Ctrl+C the pane process first.",
    ]
    note = f"wt={wt or 'missing'} ; shell={shell or 'missing'} ; split={split_mode}"
    if auto_start_claude:
        note += " ; auto-claude=on"
    if context_packet_path is not None:
        note += f" ; packet={context_packet_path.name}"
    return TerminalPlan("windows-terminal", available, True, note,
                        title, cmd, shell_cmd, close)


def build_tmux_plan(role: str, task: str, session_name: str, raw_command: list[str], *,
                    split_mode: str = "tab",
                    auto_start_claude: bool = False,
                    context_packet_path: Path | None = None,
                    no_keep_open: bool = False) -> TerminalPlan:
    # TASK-109: tmux panes close on process exit by default (no -NoExit analogue),
    # so no_keep_open needs no command change here; accepted for a uniform interface.
    tmux = which("tmux")
    title = title_for(role, task, session_name)
    # TASK-098 fix: tmux 의 -c 옵션과 별개로 PowerShell 패턴과 일관되게
    # 명령 체인 시작에 cd 추가 — 이중 보호.
    shell_cmd = build_chained_command(
        inbox_parts=raw_command,
        shell_kind="posix",
        auto_start_claude=auto_start_claude,
        context_packet_path=context_packet_path,
        working_dir=REPO_ROOT,
    )
    available = bool(tmux)
    cmd = []
    if available:
        # tmux split-window 분기: -h (수평 분할 = 옆으로) / -v (수직 분할 = 아래로)
        # tmux 의미가 wt와 반대라 매핑: pane-vertical (옆) → -h, pane-horizontal (아래) → -v
        # TASK-097 fix: 새 pane 시작 디렉토리를 프로젝트 루트로 명시 (-c <dir>).
        cwd_args = ["-c", str(REPO_ROOT)]
        if split_mode == "pane-vertical":
            cmd = [tmux, "split-window", "-h", *cwd_args, "-t", session_name, shell_cmd]
        elif split_mode == "pane-horizontal":
            cmd = [tmux, "split-window", "-v", *cwd_args, "-t", session_name, shell_cmd]
        else:  # tab (default — new session/window)
            cmd = [tmux, "new-session", "-A", "-s", session_name, *cwd_args, "-n", role, shell_cmd]
    close = [
        f"Terminal close: tmux kill-session -t {session_name}",
        "Orchestrator session close: python scripts/agent_orchestrator.py kill <agent_id>",
    ]
    note = f"tmux={tmux or 'missing'} ; split={split_mode}"
    if auto_start_claude:
        note += " ; auto-claude=on"
    if context_packet_path is not None:
        note += f" ; packet={context_packet_path.name}"
    return TerminalPlan("tmux", available, True, note,
                        title, cmd, shell_cmd, close)


def build_vscode_plan(role: str, task: str, session_name: str, raw_command: list[str]) -> TerminalPlan:
    code = which("code")
    title = title_for(role, task, session_name)
    shell_cmd = command_string(raw_command, shell="posix")
    available = bool(code)
    cmd = [code, str(REPO_ROOT), "--reuse-window"] if available else []
    close = [
        "VS Code adapter is preview-only in TASK-085.",
        "Open an integrated terminal and run the shown command; use orchestrator /kill for session state.",
    ]
    note = f"code={code or 'missing'} ; integrated terminal command preview only"
    return TerminalPlan("vscode", available, False, note, title, cmd, shell_cmd, close)


def build_plan(adapter: str, role: str, task: str, session_name: str,
               raw_command: list[str], *,
               split_mode: str = "tab",
               auto_start_claude: bool = False,
               context_packet_path: Path | None = None,
               no_keep_open: bool = False) -> TerminalPlan:
    if adapter == "manual":
        return build_manual_plan(role, task, session_name, raw_command)
    if adapter == "windows-terminal":
        return build_windows_terminal_plan(
            role, task, session_name, raw_command,
            split_mode=split_mode,
            auto_start_claude=auto_start_claude,
            context_packet_path=context_packet_path,
            no_keep_open=no_keep_open,
        )
    if adapter == "tmux":
        return build_tmux_plan(
            role, task, session_name, raw_command,
            split_mode=split_mode,
            auto_start_claude=auto_start_claude,
            context_packet_path=context_packet_path,
            no_keep_open=no_keep_open,
        )
    if adapter == "vscode":
        return build_vscode_plan(role, task, session_name, raw_command)
    raise SystemExit(f"unknown adapter: {adapter}")


def pick_adapter(requested: str, role: str, task: str, session_name: str,
                 raw_command: list[str], *,
                 split_mode: str = "tab",
                 auto_start_claude: bool = False,
                 context_packet_path: Path | None = None,
                 no_keep_open: bool = False) -> TerminalPlan:
    kwargs = {
        "split_mode": split_mode,
        "auto_start_claude": auto_start_claude,
        "context_packet_path": context_packet_path,
        "no_keep_open": no_keep_open,
    }
    if requested != "auto":
        return build_plan(requested, role, task, session_name, raw_command, **kwargs)
    os_family = detect_os_family()
    if os_family == "windows":
        order = ["windows-terminal", "vscode", "manual"]
    elif os_family in {"linux", "macos"}:
        order = ["tmux", "vscode", "manual"]
    else:
        order = ["manual"]
    last = None
    for adapter in order:
        plan = build_plan(adapter, role, task, session_name, raw_command, **kwargs)
        last = plan
        if plan.available:
            return plan
    return last or build_manual_plan(role, task, session_name, raw_command)


def render_plan(plan: TerminalPlan, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        return
    print("=" * 64)
    print("Agent Terminal Adapter -- TASK-085")
    print("=" * 64)
    print(f"  adapter             : {plan.adapter}")
    print(f"  available           : {plan.available}")
    print(f"  execution supported : {plan.execution_supported}")
    print(f"  detect note         : {plan.detect_note}")
    print(f"  title               : {plan.title}")
    print()
    print("Command preview:")
    if plan.command:
        print("  " + display_command(plan.command))
    else:
        print("  " + plan.shell_command)
    print()
    print("Close semantics:")
    for item in plan.close_preview:
        print(f"  - {item}")


def args_to_command(args: argparse.Namespace, role: str, task: str) -> list[str]:
    if args.command:
        return args.command
    if getattr(args, "observer", False):
        # TASK-103: pane is a read-only observer of the agent's runtime state.
        # Takes precedence over --worker/--auto-start-claude (it is a view, not
        # a host). The worker runs elsewhere; this pane only reflects its state.
        return observer_agent_command(role, exit_on_stop=getattr(args, "exit_on_stop", False))
    if getattr(args, "worker", False):
        # TASK-100: pane hosts an autonomous agent_worker instead of inbox-only.
        # `--worker` takes precedence over `--auto-start-claude` (set in build_parser).
        return worker_agent_command(role, getattr(args, "provider", "dummy"))
    return default_agent_command(role, task)


def resolve_context_packet(args: argparse.Namespace, role: str, task: str) -> Path | None:
    """Return path to context packet to inject into the new pane, if requested.

    --context-packet behaviour:
      - flag absent → None
      - flag without value → build packet via agent_context_packet.py and persist
      - flag with explicit path → use that path (must exist)
    """
    if not getattr(args, "context_packet", False):
        return None
    explicit = getattr(args, "context_packet_path", None)
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            print(f"\nContext packet path not found: {p}", file=sys.stderr)
            return None
        return p
    return build_context_packet(role, task)


def cmd_preview(args: argparse.Namespace) -> int:
    role = normalize_role(args.role)
    task = args.task or "none"
    raw_command = args_to_command(args, role, task)
    packet = resolve_context_packet(args, role, task)
    # TASK-100: --worker is mutually exclusive with --auto-start-claude.
    # When both set, worker wins (raw_command becomes agent_worker), and the
    # trailing `claude` line is suppressed so the pane keeps running the worker.
    auto_claude = (getattr(args, "auto_start_claude", False)
                   and not getattr(args, "worker", False)
                   and not getattr(args, "observer", False))
    # TASK-109: --observer --exit-on-stop auto-enables no-keep-open so the pane
    # closes when the observer exits; --no-keep-open forces it regardless.
    no_keep_open = getattr(args, "no_keep_open", False) or (
        getattr(args, "observer", False) and getattr(args, "exit_on_stop", False))
    plan = pick_adapter(
        args.adapter, role, task, args.session_name, raw_command,
        split_mode=getattr(args, "split_mode", "tab"),
        auto_start_claude=auto_claude,
        context_packet_path=packet,
        no_keep_open=no_keep_open,
    )
    render_plan(plan, as_json=args.json)
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    role = normalize_role(args.role)
    task = args.task or "none"
    raw_command = args_to_command(args, role, task)
    packet = resolve_context_packet(args, role, task)
    # TASK-100: --worker is mutually exclusive with --auto-start-claude.
    # When both set, worker wins (raw_command becomes agent_worker), and the
    # trailing `claude` line is suppressed so the pane keeps running the worker.
    auto_claude = (getattr(args, "auto_start_claude", False)
                   and not getattr(args, "worker", False)
                   and not getattr(args, "observer", False))
    # TASK-109: --observer --exit-on-stop auto-enables no-keep-open so the pane
    # closes when the observer exits; --no-keep-open forces it regardless.
    no_keep_open = getattr(args, "no_keep_open", False) or (
        getattr(args, "observer", False) and getattr(args, "exit_on_stop", False))
    plan = pick_adapter(
        args.adapter, role, task, args.session_name, raw_command,
        split_mode=getattr(args, "split_mode", "tab"),
        auto_start_claude=auto_claude,
        context_packet_path=packet,
        no_keep_open=no_keep_open,
    )
    render_plan(plan, as_json=args.json)
    if not args.yes:
        print("\nRefusing to launch without --yes.")
        return 2
    if not plan.available:
        print(f"\nAdapter unavailable: {plan.detect_note}")
        return 3
    if not plan.execution_supported:
        print("\nAdapter is preview-only; no process launched.")
        return 0
    try:
        subprocess.Popen(plan.command, cwd=str(REPO_ROOT))
    except Exception as exc:
        print(f"\nLaunch failed: {exc}", file=sys.stderr)
        return 4
    print("\nLaunched visible terminal adapter.")
    return 0


def cmd_close_preview(args: argparse.Namespace) -> int:
    role = normalize_role(args.role)
    task = args.task or "none"
    raw_command = args_to_command(args, role, task)
    plan = build_plan(args.adapter, role, task, args.session_name, raw_command)
    payload = {
        "adapter": plan.adapter,
        "agent_id": args.agent_id,
        "session_name": args.session_name,
        "close_preview": plan.close_preview,
    }
    if args.agent_id:
        payload["orchestrator_kill"] = [
            python_executable(),
            str(ORCHESTRATOR),
            "kill",
            args.agent_id,
        ]
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("Close preview:")
        for item in plan.close_preview:
            print(f"  - {item}")
        if args.agent_id:
            print("  - " + display_command(payload["orchestrator_kill"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_terminal",
        description="Terminal/session adapter for agent orchestrator (TASK-085).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--adapter", choices=["auto", "manual", "windows-terminal", "tmux", "vscode"],
                       default="manual")
        p.add_argument("--role", default="qa")
        p.add_argument("--task", default="none")
        p.add_argument("--session-name", default="agent_orchestrator")
        p.add_argument("--command", nargs=argparse.REMAINDER,
                       help="command to run in the terminal; defaults to role inbox")
        p.add_argument("--json", action="store_true")
        # TASK-095 자동화 갭 메우기 — 3종 옵션
        p.add_argument("--split-mode", choices=["tab", "pane-vertical", "pane-horizontal"],
                       default="tab",
                       help="윈도우 분할 모드: tab(기본 새 탭) / pane-vertical(옆으로 분할) / pane-horizontal(아래로 분할)")
        p.add_argument("--auto-start-claude", action="store_true",
                       help="새 pane에서 claude CLI를 자동 시작 (수동 typing 없음)")
        p.add_argument("--context-packet", action="store_true",
                       help="agent_context_packet.py로 컨텍스트 packet을 자동 생성해 새 pane에서 보여주고 claude 첫 프롬프트로 전달")
        p.add_argument("--context-packet-path", default=None,
                       help="--context-packet 사용 시 기존 packet 파일 경로 지정 (생략 시 자동 생성)")
        # TASK-100: pane 안에서 자율 agent_worker 실행 (claude 대체)
        p.add_argument("--worker", action="store_true",
                       help="pane에서 agent_worker.py를 자동 시작 — claude 대신 자율 워커가 inbox 폴링 + 메시지 처리. `--auto-start-claude`와 상호 배타 (worker가 우선).")
        p.add_argument("--provider", default="dummy",
                       help="--worker 사용 시 provider 이름 (기본 dummy). 예: dummy/claude/claude-agent/codex/codex-agent")
        # TASK-103: pane 을 read-only observer 로 — 워커를 호스팅하지 않고 런타임 파일만 읽어 상태 표시
        p.add_argument("--observer", action="store_true",
                       help="pane 에서 agent_observer.py 를 실행 — 워커를 호스팅하지 않고 런타임 파일(이벤트 로그·인박스)만 읽어 에이전트 상태를 표시하는 read-only 뷰. `--worker`/`--auto-start-claude` 보다 우선(뷰 전용).")
        # TASK-109: observer 종료 + pane 자동 닫기 (opt-in)
        p.add_argument("--exit-on-stop", action="store_true",
                       help="observer pane 가 watched work 종료 시 스스로 종료 (observer 에 --exit-on-stop 전달)")
        p.add_argument("--no-keep-open", action="store_true",
                       help="windows-terminal pane 의 PowerShell -NoExit 생략 → 프로세스 종료 시 pane 닫힘")

    p_preview = sub.add_parser("preview", help="print adapter command without launching")
    add_common(p_preview)
    p_preview.set_defaults(func=cmd_preview)

    p_launch = sub.add_parser("launch", help="launch visible terminal adapter (requires --yes)")
    add_common(p_launch)
    p_launch.add_argument("--yes", action="store_true",
                          help="required acknowledgement for visible terminal launch")
    p_launch.set_defaults(func=cmd_launch)

    p_close = sub.add_parser("close-preview", help="show close/kill semantics")
    add_common(p_close)
    p_close.add_argument("--agent-id", help="optional orchestrator agent_id for /kill preview")
    p_close.set_defaults(func=cmd_close_preview)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
