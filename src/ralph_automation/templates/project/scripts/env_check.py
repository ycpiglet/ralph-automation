"""
작업 환경 감지 + 프로젝트 표준 명령 안내.

세션 시작 시 또는 환경을 옮긴 직후에 이 스크립트를 한 번 실행하면,
- 현재 OS, 셸, Python, git 환경이 무엇인지
- 본 프로젝트에서 권장하는 canonical 명령은 무엇인지
- OS-native fallback이 필요한 경우 그 명령은 무엇인지
를 한 번에 확인할 수 있다.

사용:
  python scripts/env_check.py
  python scripts/env_check.py --json   # 다른 스크립트가 파싱하기 좋게 JSON 출력
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:  # Windows 콘솔(cp949)에서도 em-dash/화살표 등 UTF-8 출력 안전 (다른 스크립트와 동일 패턴)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 머신-로컬 훅 등록(.claude/ 는 gitignore). 새 환경에서 미등록이면 install_hooks.py 안내.
_HOOK_SCRIPTS = {
    "SessionStart": "scripts/session_start_hook.py",
    "UserPromptSubmit": "scripts/prompt_clarity_hook.py",
}


def _shell_token(value: str) -> str:
    return f'"{value}"' if any(ch.isspace() for ch in value) else value


def _hook_python() -> str:
    override = os.environ.get("AGENT_HOOK_PYTHON")
    if override:
        return _shell_token(override)
    return _shell_token(sys.executable or "python")


_EXPECTED_HOOKS = {event: f"{_hook_python()} {script}" for event, script in _HOOK_SCRIPTS.items()}

_DISABLED_PLUGIN_HOOKS = {
    "ralph-loop@claude-plugins-official": "Stop hook Windows bash/WSL shim risk",
    "security-guidance@claude-plugins-official": "Session/User/Post/Stop hook bash wrapper risk",
}


def hook_status() -> dict:
    settings = Path(__file__).resolve().parent.parent / ".claude" / "settings.json"
    if not settings.exists():
        return {
            "registered": False,
            "missing": list(_EXPECTED_HOOKS),
            "stale": [],
            "plugin_hook_safe": False,
            "plugin_hook_risks": list(_DISABLED_PLUGIN_HOOKS),
        }
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except Exception:
        return {
            "registered": False,
            "missing": list(_EXPECTED_HOOKS),
            "stale": [],
            "plugin_hook_safe": False,
            "plugin_hook_risks": list(_DISABLED_PLUGIN_HOOKS),
        }
    missing = []
    stale = []
    for event, cmd in _EXPECTED_HOOKS.items():
        cmds = [h.get("command") for g in data.get("hooks", {}).get(event, []) for h in g.get("hooks", [])]
        if cmd not in cmds:
            missing.append(event)
        script = _HOOK_SCRIPTS[event]
        stale.extend(
            f"{event}: {configured}"
            for configured in cmds
            if isinstance(configured, str)
            and configured != cmd
            and configured.replace("\\", "/").strip().endswith(script)
        )
    enabled = data.get("enabledPlugins", {})
    plugin_risks = [name for name in _DISABLED_PLUGIN_HOOKS if enabled.get(name) is not False]
    return {
        "registered": not missing and not stale,
        "missing": missing,
        "stale": stale,
        "plugin_hook_safe": not plugin_risks,
        "plugin_hook_risks": plugin_risks,
    }


def detect_os_family() -> str:
    s = platform.system()
    if s == "Darwin":
        return "macOS"
    if s in {"Windows", "Linux"}:
        return s
    return s or "unknown"


def detect_shell() -> str:
    """현재 실행 환경에서 추정 가능한 셸 이름. 정확한 감지는 어렵고 힌트 수준."""
    if os.name == "nt":
        # PowerShell vs cmd.exe 구분: PowerShell에서만 PSModulePath가 항상 설정됨
        if os.environ.get("PSModulePath"):
            return "PowerShell"
        if os.environ.get("ComSpec"):
            return "cmd.exe"
        return "windows-shell"
    shell = os.environ.get("SHELL", "")
    if "/zsh" in shell:
        return "zsh"
    if "/bash" in shell:
        return "bash"
    if "/fish" in shell:
        return "fish"
    if shell:
        return shell.split("/")[-1]
    return "unknown"


def detect_timezone() -> str:
    try:
        offset_seconds = -time.timezone if time.daylight == 0 else -time.altzone
        sign = "+" if offset_seconds >= 0 else "-"
        offset_seconds = abs(offset_seconds)
        hours, remainder = divmod(offset_seconds, 3600)
        minutes = remainder // 60
        return f"{time.tzname[0]} (UTC{sign}{hours:02d}:{minutes:02d})"
    except Exception:
        return "unknown"


def detect_git() -> dict:
    info: dict = {"installed": shutil.which("git") is not None}
    if not info["installed"]:
        return info
    info["version"] = _safe_cmd(["git", "--version"]) or "(unknown)"
    info["user_name"] = _safe_cmd(["git", "config", "user.name"]) or "(unset)"
    info["user_email"] = _safe_cmd(["git", "config", "user.email"]) or "(unset)"
    info["branch"] = _safe_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "(not in repo)"
    info["remote"] = _safe_cmd(["git", "config", "--get", "remote.origin.url"]) or "(no remote)"
    return info


def _safe_cmd(args: list[str]) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def canonical_commands() -> dict:
    """OS와 무관하게 본 프로젝트가 권장하는 단일 명령들."""
    return {
        "timestamp_local": "python scripts/now.py",
        "timestamp_utc": "python scripts/now.py --utc",
        "date_only": "python scripts/now.py --date",
        "agent_docs_check": "python scripts/check_agent_docs.py",
        "task_filter": "python scripts/query_tasks.py [--status X] [--owner X] [--tag X] [--priority X]",
        "generate_views": "python scripts/generate_views.py",
        "env_check": "python scripts/env_check.py",
        "e2e_test": "pytest scripts/test_e2e.py -v",
        "deployment_check": "python scripts/check_deployment.py",
        "docs_sync": "python scripts/sync_docs_to_public.py",
    }


def os_fallback_commands(os_family: str) -> dict:
    """Python을 못 쓰는 응급 상황의 OS-native 대체 명령."""
    if os_family == "Windows":
        return {
            "timestamp_local": 'Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"',
            "timestamp_utc": '(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")',
            "date_only": "Get-Date -Format yyyy-MM-dd",
            "list_dir": "Get-ChildItem",
            "find_file": "Get-ChildItem -Recurse -Filter <pattern>",
        }
    if os_family == "macOS":
        return {
            "timestamp_local": "date +'%Y-%m-%dT%H:%M:%S%z' | sed -E 's/([+-][0-9]{2})([0-9]{2})$/\\1:\\2/'",
            "timestamp_utc": "date -u +'%Y-%m-%dT%H:%M:%SZ'",
            "date_only": "date +'%Y-%m-%d'",
            "list_dir": "ls -la",
            "find_file": "find . -name <pattern>",
            "note": "BSD date는 GNU date의 %:z를 지원하지 않으므로 sed로 콜론 삽입",
        }
    if os_family == "Linux":
        return {
            "timestamp_local": 'date +"%Y-%m-%dT%H:%M:%S%:z"',
            "timestamp_utc": 'date -u +"%Y-%m-%dT%H:%M:%SZ"',
            "date_only": 'date +"%Y-%m-%d"',
            "list_dir": "ls -la",
            "find_file": "find . -name <pattern>",
        }
    return {"note": f"Unknown OS family '{os_family}', use Python canonical commands."}


def collect() -> dict:
    os_family = detect_os_family()
    return {
        "os_family": os_family,
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "shell_hint": detect_shell(),
        "timezone": detect_timezone(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
        "git": detect_git(),
        "canonical_commands": canonical_commands(),
        "os_fallback_commands": os_fallback_commands(os_family),
        "hooks": hook_status(),
    }


def print_human(info: dict) -> None:
    print("=" * 64)
    print("작업 환경 감지 (Environment Snapshot)")
    print("=" * 64)
    print(f"  OS family       : {info['os_family']} {info['os_release']}")
    print(f"  Machine         : {info['machine']}")
    print(f"  Shell hint      : {info['shell_hint']}")
    print(f"  Timezone        : {info['timezone']}")
    print(f"  Python          : {info['python_version']}  ({info['python_executable']})")
    print(f"  CWD             : {info['cwd']}")

    git = info["git"]
    print("\nGit:")
    if not git.get("installed"):
        print("  not installed")
    else:
        print(f"  version         : {git.get('version', '-')}")
        print(f"  user.name       : {git.get('user_name', '-')}")
        print(f"  user.email      : {git.get('user_email', '-')}")
        print(f"  branch          : {git.get('branch', '-')}")
        print(f"  remote          : {git.get('remote', '-')}")

    print("\nCanonical commands (OS 무관, 모든 환경에서 동일):")
    for k, v in info["canonical_commands"].items():
        print(f"  {k:20s}: {v}")

    print(f"\nOS-native fallback ({info['os_family']}):")
    for k, v in info["os_fallback_commands"].items():
        print(f"  {k:20s}: {v}")

    hooks = info["hooks"]
    print("\nClaude Code 훅 (머신-로컬 등록 — .claude/ 는 gitignore):")
    if hooks["registered"]:
        print("  registered      : ok (복도 게시판 BACKLOG.md + 명확성 훅 자동 발화)")
    else:
        print(f"  registered      : MISSING {hooks['missing']}")
        print("  → `python scripts/install_hooks.py` 실행(이 PC에 1회). 그래야 세션 시작 시 게시판 자동 노출.")
    if hooks.get("stale"):
        print(f"  stale commands  : {hooks['stale']}")
        print("  → `python scripts/install_hooks.py` 실행(깨진 python hook 명령 교체).")
    if hooks["plugin_hook_safe"]:
        print("  plugin hooks    : ok (반복 실패 플러그인 hook 비활성화)")
    else:
        print(f"  plugin hooks    : RISK {hooks['plugin_hook_risks']}")
        print("  → `python scripts/install_hooks.py` 실행(Windows bash hook 실패 재발 방지).")

    print("\n원칙: 우선순위는 canonical 명령. Python을 못 쓰는 경우에만 OS fallback 사용.")
    print("자세한 규칙은 AGENTS.md §12 (타임스탬프 표준) 및 §1 공통 시작 프로토콜 참조.\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect environment and recommended commands.")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)
    info = collect()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print_human(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
