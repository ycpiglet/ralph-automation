"""install_hooks 멱등 등록 + 기존 키 보존 + --check 종료코드 고정.

.claude/ 가 gitignore 라 등록이 머신-로컬인 갭을 메우는 헬퍼 — 어느 PC 든 같은 게시판을
자동 발화시키는 한 조각(AUDIT-2026-06-04-002).
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_hooks.py"


def _hook_python() -> str:
    value = str(Path(sys.executable))
    return f'"{value}"' if any(ch.isspace() for ch in value) else value


SESSION_CMD = f"{_hook_python()} scripts/session_start_hook.py"
PROMPT_CMD = f"{_hook_python()} scripts/prompt_clarity_hook.py"


def _run(args, tmp):
    # 테스트는 절대 실 .claude 를 건드리지 않게 settings/commands 둘 다 tmp 로
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args,
         "--settings", str(tmp / "settings.json"),
         "--commands-dir", str(tmp / "commands")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )


def _commands(data, event):
    return [h["command"] for g in data.get("hooks", {}).get(event, []) for h in g.get("hooks", [])]


def test_registers_into_empty(tmp_path):
    assert _run([], tmp_path).returncode == 0
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert SESSION_CMD in _commands(data, "SessionStart")
    assert PROMPT_CMD in _commands(data, "UserPromptSubmit")
    assert f"Bash({SESSION_CMD})" in data["permissions"]["allow"]
    assert data["enabledPlugins"]["ralph-loop@claude-plugins-official"] is False
    assert data["enabledPlugins"]["security-guidance@claude-plugins-official"] is False
    # 슬래시 커맨드도 설치
    assert (tmp_path / "commands" / "backlog.md").exists()
    assert (tmp_path / "commands" / "task.md").exists()
    assert (tmp_path / "commands" / "schedule-status.md").exists()
    assert (tmp_path / "commands" / "schedule-local.md").exists()
    assert (tmp_path / "commands" / "events.md").exists()
    backlog = (tmp_path / "commands" / "backlog.md").read_text(encoding="utf-8")
    assert "## 한눈에 보기" in backlog
    assert "평문 리스트로 재요약하지 않는다" in backlog
    schedule_local = (tmp_path / "commands" / "schedule-local.md").read_text(encoding="utf-8")
    assert "local_schedule_daemon.py status" in schedule_local


def test_idempotent(tmp_path):
    _run([], tmp_path)
    before = (tmp_path / "settings.json").read_text(encoding="utf-8")
    _run([], tmp_path)
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == before  # 재실행해도 변화 없음


def test_preserves_existing_keys(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"enabledPlugins": {"x@y": True}}), encoding="utf-8")
    _run([], tmp_path)
    data = json.loads(s.read_text(encoding="utf-8"))
    assert data["enabledPlugins"]["x@y"] is True  # 기존 키 보존(덮어쓰지 않음)
    assert data["enabledPlugins"]["ralph-loop@claude-plugins-official"] is False
    assert data["enabledPlugins"]["security-guidance@claude-plugins-official"] is False
    assert SESSION_CMD in _commands(data, "SessionStart")


def test_check_exit_codes(tmp_path):
    assert _run(["--check"], tmp_path).returncode == 1  # 빈 → 미등록(훅·커맨드)
    _run([], tmp_path)
    assert _run(["--check"], tmp_path).returncode == 0  # 등록 후 ok


def test_check_fails_and_install_repairs_missing_permissions(tmp_path):
    _run([], tmp_path)
    settings_path = tmp_path / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["permissions"]["allow"] = []
    settings_path.write_text(json.dumps(data), encoding="utf-8")

    check = _run(["--check"], tmp_path)
    assert check.returncode == 1
    assert f"permission Bash({SESSION_CMD})" in check.stdout

    assert _run([], tmp_path).returncode == 0
    repaired = json.loads(settings_path.read_text(encoding="utf-8"))
    assert f"Bash({SESSION_CMD})" in repaired["permissions"]["allow"]
    assert f"Bash({PROMPT_CMD})" in repaired["permissions"]["allow"]


def test_check_fails_and_install_disables_unstable_plugin_hooks(tmp_path):
    _run([], tmp_path)
    settings_path = tmp_path / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["enabledPlugins"]["security-guidance@claude-plugins-official"] = True
    settings_path.write_text(json.dumps(data), encoding="utf-8")

    check = _run(["--check"], tmp_path)
    assert check.returncode == 1
    assert "disabled plugin security-guidance@claude-plugins-official" in check.stdout

    assert _run([], tmp_path).returncode == 0
    repaired = json.loads(settings_path.read_text(encoding="utf-8"))
    assert repaired["enabledPlugins"]["security-guidance@claude-plugins-official"] is False


def test_install_replaces_stale_bare_python_hook_commands(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {
            "allow": [
                "Bash(python scripts/session_start_hook.py)",
                "Bash(python scripts/prompt_clarity_hook.py)",
            ],
        },
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "python scripts/session_start_hook.py"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python scripts/prompt_clarity_hook.py"}]}],
        },
    }), encoding="utf-8")

    check = _run(["--check"], tmp_path)
    assert check.returncode == 1
    assert "stale hook SessionStart: python scripts/session_start_hook.py" in check.stdout

    assert _run([], tmp_path).returncode == 0
    repaired = json.loads(settings_path.read_text(encoding="utf-8"))
    assert _commands(repaired, "SessionStart") == [SESSION_CMD]
    assert _commands(repaired, "UserPromptSubmit") == [PROMPT_CMD]
    assert "Bash(python scripts/session_start_hook.py)" not in repaired["permissions"]["allow"]
    assert f"Bash({SESSION_CMD})" in repaired["permissions"]["allow"]
