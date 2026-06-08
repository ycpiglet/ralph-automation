#!/usr/bin/env python3
"""Shared filesystem watcher helper (TASK-107).

Extracted from the duplicated watchdog code in agent_worker (TASK-105) and
agent_observer (TASK-106). Both subscribe to one or more directories and set a
threading.Event on any change, so a loop can wake immediately instead of
polling. watchdog is lazy-imported: callers fall back to polling when it is
absent.
"""

from __future__ import annotations

import threading
from pathlib import Path


def start_fs_watcher(dirs, signal: threading.Event, log_fn=None):
    """Watch `dirs` for changes; set `signal` on any event.

    Returns the started watchdog Observer (caller stops it), or None if watchdog
    is unavailable — in which case the caller silently falls back to polling.
    Read-only: the watcher only triggers wakeups, it never writes.
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception as exc:  # not installed / import error → polling fallback
        if log_fn:
            log_fn(f"watchdog unavailable ({exc}); falling back to polling")
        return None

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            signal.set()

    observer = Observer()
    for d in dirs:
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        observer.schedule(_Handler(), str(d), recursive=False)
    observer.start()
    return observer
