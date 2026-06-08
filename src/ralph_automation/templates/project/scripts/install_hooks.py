#!/usr/bin/env python3
"""이 repo 의 Claude Code 훅을 로컬 `.claude/settings.json` 에 멱등 등록한다.

왜 필요한가: `.claude/` 는 `.gitignore` 라 **머신-로컬**이다(설계상). 그래서 훅 *스크립트*
(`scripts/session_start_hook.py` 등)는 git 으로 공유되지만 *등록*은 PC 마다 따로 해야 한다.
다른 PC/클론에서 이걸 한 번 실행하면 복도 게시판(BACKLOG.md sync-on-start)과 명확성 훅이
자동 발화한다 — "다른 PC/세션도 같은 게시판" 을 완성하는 한 조각(AUDIT-2026-06-04-002).

settings.json 직접 편집은 self-escalation(R3)이라 에이전트가 임의로 하지 않는다 —
**Owner/사용자가 본 스크립트를 명시적으로 실행**하는 것이 등록의 consent 다.

사용:
  python scripts/install_hooks.py            # 멱등 등록(이미 있으면 그대로)
  python scripts/install_hooks.py --check    # 등록 여부만 보고(미등록 시 exit 1)

Windows 에서 `python` 이 PATH 에 없으면 현재 Python 절대경로로 실행한다. 이 스크립트는
hook 명령도 bare `python` 이 아니라 실행 중인 `sys.executable` 절대경로로 등록한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = REPO_ROOT / ".claude" / "settings.json"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "scripts/session_start_hook.py",
    "UserPromptSubmit": "scripts/prompt_clarity_hook.py",
}


def _shell_token(value: str) -> str:
    return f'"{value}"' if any(ch.isspace() for ch in value) else value


def _hook_python() -> str:
    """Return a hook-safe Python executable for this machine.

    Hook runners may not inherit the same PATH as an interactive shell. A bare
    `python` command repeatedly failed on Windows, so install machine-local
    hooks with the interpreter that is running this installer.
    """
    override = os.environ.get("AGENT_HOOK_PYTHON")
    if override:
        return _shell_token(override)
    return _shell_token(sys.executable or "python")


def _hook_command(script: str) -> str:
    return f"{_hook_python()} {script}"


# event -> hook command. 신규 훅을 늘리려면 HOOK_SCRIPTS 에만 추가한다.
HOOKS: dict[str, str] = {event: _hook_command(script) for event, script in HOOK_SCRIPTS.items()}

# Windows 환경에서 반복적으로 code 1 을 낸 Claude plugin hook.
# repo-local 훅은 유지하고, 이 advisory plugin hook 들만 머신 설정에서 끈다.
DISABLED_PLUGIN_HOOKS: dict[str, str] = {
    "ralph-loop@claude-plugins-official": "Stop hook 이 Windows bash/WSL shim 에 걸릴 수 있음",
    "security-guidance@claude-plugins-official": "SessionStart/UserPromptSubmit/PostToolUse/Stop hook 이 bash wrapper 를 호출함",
}

# name -> 슬래시 커맨드 본문(.claude/commands/<name>.md). .claude 는 gitignore 라 PC 마다 설치.
COMMANDS: dict[str, str] = {
    "backlog": (
        "---\n"
        "description: 열린 작업 단일 포인터(BACKLOG.md) 의사결정 보드 + 런타임 신호\n"
        "---\n\n"
        "`python scripts/generate_views.py` 로 BACKLOG 생성물을 최신화한 뒤, "
        "`agents/lead_engineer/tasks/BACKLOG.md` 의 `## 한눈에 보기`와 `## 결정 레인`을 "
        "표 구조 그대로 보여라. 평문 리스트로 재요약하지 않는다(COMPOUND-035). "
        "추가로 `python scripts/backlog_sweep.py` 를 실행해 due-check/doc_health 런타임 신호만 덧붙인다.\n"
    ),
    "digest": (
        "---\n"
        "description: secretary — Owner 데스크 요약(열린 작업·결정 대기·예정 스케줄·리스크) 생성\n"
        "---\n\n"
        "`python scripts/secretary_digest.py` 를 실행해 `agents/owner/digest/DIGEST-{today}.md` 를 "
        "생성하고, Bottom Line 을 그대로 보고하라. secretary 는 R1(보고·상기·제안)만 — 결정·배정·"
        "구현은 하지 않는다. 본문만 보려면 `--stdout`.\n"
    ),
    "schedule": (
        "---\n"
        "description: 자율 스케줄 레지스트리(SCHEDULE.yml) 조회·CRUD\n"
        "---\n\n"
        "`python scripts/schedule.py list` 로 등록된 스케줄을 보여라. 추가/수정은 "
        "`schedule.py add|update|enable|disable|remove`. 발화 계획은 "
        "`python scripts/auto_runner.py --from-schedule`(dry-run). 무인 cron 발화(enabled + mode=auto)는 "
        "R3 — Owner 가 routine(CronCreate)으로 등록한다(agents/lead_engineer/SCHEDULE-ROUTINE.md).\n"
    ),
    "schedule-status": (
        "---\n"
        "description: 자율 스케줄 + OS 트리거 상태 대시보드\n"
        "---\n\n"
        "`python scripts/schedule_task.py status` 를 실행해 OS 작업 등록 여부, 활성 schedule 수, "
        "최근 발화 보고서를 보여라. `register|unregister|run` 은 OS 트리거 계층이라 R3 경계가 "
        "있다. Owner 가 명시 실행한 경우에만 사용하고, 일반 세션에서는 status 조회만 한다.\n"
    ),
    "schedule-local": (
        "---\n"
        "description: local 상시 스케줄 daemon 상태/실행(Windows Task Scheduler 255 fallback)\n"
        "---\n\n"
        "`python scripts/local_schedule_daemon.py status` 로 local daemon heartbeat를 확인하라. "
        "즉시 R1 notify smoke는 `python scripts/local_schedule_daemon.py tick --force`. "
        "상시 실행은 별도 터미널/백그라운드에서 "
        "`python scripts/local_schedule_daemon.py watch --interval 60 --run-now`. "
        "이 경로는 현재 사용자 세션에서만 살아 있으므로 PC 전원과 앱/터미널 세션이 켜져 있어야 한다.\n"
    ),
    "task": (
        "---\n"
        "description: 구조화 TASK 조회·상태 전이 API(task_api.py)\n"
        "---\n\n"
        "`python scripts/task_api.py query` 로 TASK 를 조회하라. 단일 TASK 는 "
        "`python scripts/task_api.py get TASK-NNN`. 상태 변경은 "
        "`python scripts/task_api.py set-status TASK-NNN <대기|진행 중|완료|보류>` 를 사용하되, "
        "완료 전이는 TASK 완료 기록·검증·리뷰·AUDIT ceremony 를 먼저 채운 뒤 실행한다.\n"
    ),
    "events": (
        "---\n"
        "description: TASK 변경 이벤트 로그 조회(tasks.events.jsonl)\n"
        "---\n\n"
        "`python scripts/task_events.py --tail 20` 로 최근 TASK 변경 이벤트를 조회하라. "
        "특정 TASK 는 `--task TASK-NNN`, 기계 처리는 `--json`. 이벤트 로그는 per-machine "
        "runtime append-only 이며 canonical 상태는 TASK markdown/frontmatter 이다.\n"
    ),
    "macro": (
        "---\n"
        "description: 반복 요청 감지 → 함수화 제안(propose-only, 생성 안 함)\n"
        "---\n\n"
        "`python scripts/macro_detect.py` 를 실행해 반복 의도 후보를 *제안만* 하라. "
        "임계 ≥3회/≥2일 미만은 침묵(cry-wolf 억제). 입력=TASK/MEETING 기록만(프롬프트·시크릿 비대상). "
        "**자동 생성 금지** — 함수/skill 화는 R3(정상 plan→approve). 채택 시 사람이 TASK 로 올린다.\n"
    ),
}


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"[install_hooks] {path} 파싱 실패(수동 확인 필요, 덮어쓰지 않음): {exc}")


def _has_hook(settings: dict, event: str, cmd: str) -> bool:
    for group in settings.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            if h.get("command") == cmd:
                return True
    return False


def _same_repo_hook_script(command: str, script: str) -> bool:
    return command.replace("\\", "/").strip().endswith(script)


def _remove_stale_hook_commands(settings: dict, event: str, cmd: str) -> bool:
    script = HOOK_SCRIPTS[event]
    groups = settings.setdefault("hooks", {}).setdefault(event, [])
    changed = False
    new_groups = []
    for group in groups:
        hooks = []
        for hook in group.get("hooks", []):
            hook_cmd = hook.get("command")
            if isinstance(hook_cmd, str) and hook_cmd != cmd and _same_repo_hook_script(hook_cmd, script):
                changed = True
                continue
            hooks.append(hook)
        if hooks:
            new_group = dict(group)
            new_group["hooks"] = hooks
            new_groups.append(new_group)
        else:
            changed = True
    if changed:
        settings["hooks"][event] = new_groups
    return changed


def _stale_hook_commands(settings: dict) -> list[tuple[str, str]]:
    stale = []
    for event, expected in HOOKS.items():
        script = HOOK_SCRIPTS[event]
        for group in settings.get("hooks", {}).get(event, []):
            for hook in group.get("hooks", []):
                cmd = hook.get("command")
                if isinstance(cmd, str) and cmd != expected and _same_repo_hook_script(cmd, script):
                    stale.append((event, cmd))
    return stale


def _ensure_hook(settings: dict, event: str, cmd: str) -> bool:
    """없으면 추가하고 True, 이미 있으면 False(멱등)."""
    changed = _remove_stale_hook_commands(settings, event, cmd)
    if _has_hook(settings, event, cmd):
        return changed
    settings.setdefault("hooks", {}).setdefault(event, []).append(
        {"hooks": [{"type": "command", "command": cmd}]}
    )
    return True


def _permission_cmd(entry: str) -> str | None:
    if entry.startswith("Bash(") and entry.endswith(")"):
        return entry[5:-1]
    return None


def _stale_permissions(settings: dict) -> list[str]:
    stale = []
    allow = settings.get("permissions", {}).get("allow", [])
    for event, expected in HOOKS.items():
        script = HOOK_SCRIPTS[event]
        for entry in allow:
            cmd = _permission_cmd(entry)
            if cmd and cmd != expected and _same_repo_hook_script(cmd, script):
                stale.append(entry)
    return stale


def _ensure_permission(settings: dict, cmd: str, script: str) -> bool:
    """훅 명령이 권한 프롬프트 없이 돌도록 allow 에 추가(멱등)."""
    entry = f"Bash({cmd})"
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    before = list(allow)
    allow[:] = [
        item for item in allow
        if not (
            (stale_cmd := _permission_cmd(item))
            and stale_cmd != cmd
            and _same_repo_hook_script(stale_cmd, script)
        )
    ]
    changed = allow != before
    if entry in allow:
        return changed
    allow.append(entry)
    return True


def _plugin_hook_risks(settings: dict) -> list[str]:
    enabled = settings.get("enabledPlugins", {})
    return [name for name in DISABLED_PLUGIN_HOOKS if enabled.get(name) is not False]


def _ensure_plugin_disabled(settings: dict, name: str) -> bool:
    enabled = settings.setdefault("enabledPlugins", {})
    if enabled.get(name) is False:
        return False
    enabled[name] = False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="repo 훅을 로컬 .claude/settings.json 에 멱등 등록")
    ap.add_argument("--check", action="store_true", help="등록 여부만 보고(미등록 시 exit 1)")
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="대상 settings.json(테스트용)")
    ap.add_argument("--commands-dir", type=Path, default=DEFAULT_SETTINGS.parent / "commands",
                    help="슬래시 커맨드 디렉토리(테스트용)")
    args = ap.parse_args(argv)

    settings = _load(args.settings)

    missing = [(ev, cmd) for ev, cmd in HOOKS.items() if not _has_hook(settings, ev, cmd)]
    missing_perms = [
        cmd for cmd in HOOKS.values()
        if f"Bash({cmd})" not in settings.get("permissions", {}).get("allow", [])
    ]
    stale_hooks = _stale_hook_commands(settings)
    stale_perms = _stale_permissions(settings)
    plugin_risks = _plugin_hook_risks(settings)
    missing_cmds = []
    for name, body in COMMANDS.items():
        f = args.commands_dir / f"{name}.md"
        if not f.exists() or f.read_text(encoding="utf-8") != body:
            missing_cmds.append(name)

    if args.check:
        if missing or missing_perms or stale_hooks or stale_perms or plugin_risks or missing_cmds:
            print("[install_hooks] 미등록:")
            for ev, cmd in missing:
                print(f"  - hook {ev}: {cmd}")
            for cmd in missing_perms:
                print(f"  - permission Bash({cmd})")
            for ev, cmd in stale_hooks:
                print(f"  - stale hook {ev}: {cmd}")
            for entry in stale_perms:
                print(f"  - stale permission {entry}")
            for name in plugin_risks:
                print(f"  - disabled plugin {name} ({DISABLED_PLUGIN_HOOKS[name]})")
            for name in missing_cmds:
                print(f"  - command /{name}")
            print("  → `python scripts/install_hooks.py` 실행")
            return 1
        print("[install_hooks] 모든 훅·커맨드 등록됨(ok)")
        return 0

    changed = False
    settings_changed = False
    for ev, cmd in HOOKS.items():
        if _ensure_hook(settings, ev, cmd):
            print(f"[install_hooks] + hook {ev}: {cmd}")
            settings_changed = True
        if _ensure_permission(settings, cmd, HOOK_SCRIPTS[ev]):
            print(f"[install_hooks] + permission Bash({cmd})")
            settings_changed = True

    for name, reason in DISABLED_PLUGIN_HOOKS.items():
        if _ensure_plugin_disabled(settings, name):
            print(f"[install_hooks] - plugin hook {name}: disabled ({reason})")
            settings_changed = True

    if settings_changed:
        args.settings.parent.mkdir(parents=True, exist_ok=True)
        args.settings.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[install_hooks] {args.settings} 갱신 완료.")
        changed = True

    for name in missing_cmds:
        args.commands_dir.mkdir(parents=True, exist_ok=True)
        (args.commands_dir / f"{name}.md").write_text(COMMANDS[name], encoding="utf-8")
        print(f"[install_hooks] + command /{name}")
        changed = True

    print("[install_hooks] 새 세션부터 복도 게시판 자동 발화 + 슬래시 커맨드 사용 가능."
          if changed else "[install_hooks] 이미 전부 등록됨 — 변경 없음(멱등).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
