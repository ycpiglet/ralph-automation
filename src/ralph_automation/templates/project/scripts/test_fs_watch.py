"""Unit tests for the shared fs_watch helper (TASK-107)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fs_watch  # noqa: E402


def test_returns_none_without_watchdog(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("watchdog"):
            raise ImportError("simulated: watchdog not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    logs = []
    observer = fs_watch.start_fs_watcher([Path(".")], threading.Event(), log_fn=logs.append)
    assert observer is None
    assert any("watchdog unavailable" in m for m in logs)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("watchdog") is None,
    reason="watchdog not installed",
)
def test_signals_on_change_in_any_watched_dir(tmp_path):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    signal = threading.Event()
    observer = fs_watch.start_fs_watcher([d1, d2], signal)  # dirs auto-created
    assert observer is not None
    try:
        (d2 / "touch.txt").write_text("x", encoding="utf-8")
        assert signal.wait(timeout=5.0), "watcher did not signal on change in 2nd dir"
    finally:
        observer.stop()
        observer.join(timeout=2.0)
