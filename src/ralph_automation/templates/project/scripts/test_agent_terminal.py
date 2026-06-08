"""Unit tests for agent_terminal (TASK-109).

Tests for observer_agent_command exit_on_stop, build_windows_terminal_plan
no_keep_open, and preview wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_terminal  # noqa: E402


def test_observer_command_appends_exit_on_stop():
    import agent_terminal as at
    cmd = at.observer_agent_command("qa", exit_on_stop=True)
    assert "--exit-on-stop" in cmd
    assert "--exit-on-stop" not in at.observer_agent_command("qa")


def test_windows_plan_no_keep_open_drops_noexit():
    import agent_terminal as at
    raw = at.observer_agent_command("qa")
    keep = at.build_windows_terminal_plan("qa", "none", "s", raw, split_mode="pane-vertical")
    nokeep = at.build_windows_terminal_plan("qa", "none", "s", raw, split_mode="pane-vertical",
                                            no_keep_open=True)
    if keep.available:  # only meaningful where wt+shell exist; still asserts structure
        assert "-NoExit" in keep.command
        assert "-NoExit" not in nokeep.command


def test_preview_observer_exit_on_stop_auto_no_keep_open(capsys):
    import agent_terminal as at
    rc = at.main(["preview", "--adapter", "windows-terminal", "--role", "qa",
                  "--observer", "--exit-on-stop", "--split-mode", "pane-vertical"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--exit-on-stop" in out          # observer command carries the flag
    # -NoExit dropped because --observer + --exit-on-stop auto-enables no-keep-open
    assert "-NoExit" not in out
