"""Shared in-loop guardrail for agentic provider run() loops (TASK-211, CYCLE-079).

CYCLE-075 bounded a single dispatch's worst-case *token* spend with
DISPATCH_PER_CALL_CAP. This adds the two remaining in-loop guards it deferred:

  - STOP_LOOP polling: a long-running dispatch can be halted mid-flight by an
    operator dropping a stop file, instead of only between dispatches. The check
    runs before each (billable) API call, so the halt latency drops from "one
    whole dispatch" to "one tool batch".
  - Wall-clock deadline: an absolute per-dispatch time budget bounds a dispatch
    that is slow rather than token-hungry (e.g. a hung tool or a very long tool
    chain). Opt-in via DISPATCH_DEADLINE_SECONDS (0/unset = disabled), because a
    wrong default would cut legitimate long agent runs.

The helper is pure given (started_at, now, stop_files) so it is unit-testable
with no real clock or filesystem coupling.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Same files auto_dispatch / the orchestrator honour, so a single stop signal
# halts both the cross-dispatch runner and the in-dispatch provider loop.
STOP_FILES = (
    _REPO_ROOT / "agents" / "runtime" / "STOP_LOOP",
    _REPO_ROOT / ".orchestrator-stop",
)


def deadline_seconds() -> float:
    """Per-dispatch wall-clock budget in seconds. 0 (or unset/invalid) disables
    the deadline — token cap + STOP_LOOP still apply."""
    try:
        return max(0.0, float(os.environ.get("DISPATCH_DEADLINE_SECONDS", "0") or 0))
    except (TypeError, ValueError):
        return 0.0


def loop_guard_abort_reason(
    started_at: float,
    *,
    now: float,
    stop_files=None,
    deadline: float | None = None,
) -> str | None:
    """Return a human-readable abort reason if the dispatch loop should stop
    BEFORE the next billable call, else None to continue.

    Checks, in order: a stop file is present, then the wall-clock deadline has
    elapsed. `started_at`/`now` are monotonic seconds (caller passes
    time.monotonic()).

    Pure given all four arguments. When `stop_files`/`deadline` are left None
    they resolve to the module `STOP_FILES` / `DISPATCH_DEADLINE_SECONDS` env at
    call time — that is the only global read, and it is re-read each call so a
    test can override either without re-importing.
    """
    files = STOP_FILES if stop_files is None else stop_files
    for p in files:
        try:
            if Path(p).exists():
                return f"stop file present ({Path(p).name})"
        except Exception:
            continue
    budget = deadline_seconds() if deadline is None else deadline
    if budget > 0 and (now - started_at) >= budget:
        return f"wall-clock deadline exceeded ({budget:.0f}s)"
    return None
