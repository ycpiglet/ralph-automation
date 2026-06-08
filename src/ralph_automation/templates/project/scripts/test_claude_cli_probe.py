from __future__ import annotations

import json

import scripts.claude_cli_probe as probe


def test_probe_skips_live_smoke_by_default():
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return {
            "claude": r"C:\fake\claude.exe",
            "pwsh": r"C:\fake\pwsh.exe",
        }.get(name)

    def fake_run(args: list[str], timeout: float) -> probe.CommandResult:
        calls.append(args)
        return probe.CommandResult(0, "2.1.163 (Claude Code)\n", "")

    result = probe.build_probe(
        which=fake_which,
        run=fake_run,
        os_task={"available": True, "registered": False},
        schedules=[{"enabled": True}, {"enabled": False}],
    )

    assert result["claude"]["present"] is True
    assert result["claude"]["version"] == "2.1.163 (Claude Code)"
    assert result["live_smoke"]["status"] == "skipped"
    assert not any("-p" in call for call in calls)
    assert result["scheduler"]["enabled_schedules"] == 1


def test_live_smoke_uses_noninteractive_json_command_only_when_requested():
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return r"C:\fake\claude.exe" if name == "claude" else None

    def fake_run(args: list[str], timeout: float) -> probe.CommandResult:
        calls.append(args)
        if "--version" in args:
            return probe.CommandResult(0, "2.1.163 (Claude Code)\n", "")
        return probe.CommandResult(0, json.dumps({"result": "OK"}), "")

    result = probe.build_probe(
        which=fake_which,
        run=fake_run,
        os_task={"available": True, "registered": True},
        schedules=[],
        include_live_smoke=True,
        live_prompt="Reply OK",
    )

    assert result["live_smoke"]["status"] == "ok"
    assert calls[-1] == [
        r"C:\fake\claude.exe",
        "-p",
        "Reply OK",
        "--output-format",
        "json",
        "--max-turns",
        "1",
    ]


def test_recommendations_mark_scheduler_and_pane_boundaries():
    def fake_which(name: str) -> str | None:
        return r"C:\fake\claude.exe" if name == "claude" else None

    result = probe.build_probe(
        which=fake_which,
        run=lambda args, timeout: probe.CommandResult(0, "2.1.163\n", ""),
        os_task={"available": True, "registered": False},
        schedules=[{"enabled": True}],
    )

    recommendations = "\n".join(result["recommendations"])
    assert "schedule_task.py register" in recommendations
    assert "interactive pane" in recommendations
    assert "agent_worker" in recommendations
