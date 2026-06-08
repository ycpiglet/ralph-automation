#!/usr/bin/env python3
"""Ralph-style meta-loop runner (TASK-070).

OS-비종속 canonical Python runner. mode 별 iteration 흐름을 일관된 stop condition·
safety gate 위에서 실행한다. `loop.sh`/`loop.ps1` 같은 OS 셸 스크립트는 본
스크립트의 thin wrapper 만 — 로직 중복 금지 (TASK-070 인수사항 §5).

첫 컷 범위 (CLAUDE.md "Simplicity First"):
  - `plan` / `build` 모드: dry-run preview 안전 동작.
  - `review` / `audit` / `retro`: scaffolding 만, NotImplementedError 명시.
  - stop condition 5종: max-iterations, max-failures, stop-file, dirty-worktree,
    safety_gate emergency_stop.
  - 이벤트 로그: `agents/runtime/events/agent_loop-{date}.jsonl`.

TASK-070 인수사항 적용:
  §1 메시지 버스 공유 — 본 runner 가 직접 메시지를 만들 때는 9필드 frontmatter
     스키마를 따른다. 첫 컷은 외부 명령 (`agent_orchestrator`) 위임으로 우회.
  §2 Safety gate 통과 — 각 iteration 전 `check_emergency_stop` 호출.
  §3 Role registry 활용 — role 해소가 필요할 때 `agent_context_packet` 위임.
  §4 Handoff Protocol — 토큰 한계 진입 시 §13 4단 구조 트리거 (수동).
  §5 canonical 단일 구현 — 본 파일 외 다른 언어 구현 금지.

검증:
  python scripts/agent_loop.py --help
  python scripts/agent_loop.py --mode plan --max-iterations 1 --dry-run
  python scripts/agent_loop.py --mode build --max-iterations 1 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_ROOT / "agents" / "runtime"
EVENTS_DIR = RUNTIME_DIR / "events"
STOP_FILE = RUNTIME_DIR / "STOP_LOOP"
ORCHESTRATOR_STOP_FILE = REPO_ROOT / ".orchestrator-stop"
HEARTBEAT_FILE = RUNTIME_DIR / "heartbeat.json"

VALID_MODES = ("plan", "build", "review", "audit", "retro")
IMPLEMENTED_MODES = ("plan", "build", "review", "audit", "retro")
HARD_MAX_ITERATIONS = 12
EXPLICIT_AUTH_DEFAULT_ITERATIONS = 5
HARD_DISPATCH_SESSION_BUDGET = 50_000
HARD_DISPATCH_MAX = 5


@dataclass
class LoopConfig:
    mode: str
    max_iterations: int = 1
    max_failures: int = 2
    dry_run: bool = True
    allow_dirty: bool = False
    stop_file: Path = STOP_FILE
    # TASK-117 — long-running mode support
    heartbeat_interval: int = 1          # write heartbeat every N iterations (0 = disabled)
    heartbeat_file: Path = HEARTBEAT_FILE
    backoff_max_seconds: int = 0         # 0 = no backoff; >0 = exponential 2,4,8... capped
    # TASK-213 — build mode write-back dispatch (opt-in, default off = current behavior)
    dispatch: bool = False               # run one bounded write-back auto_dispatch pass
    dispatch_provider: str = "dummy"     # dummy = no live spend; live needs DISPATCH_ENABLE_LIVE
    dispatch_max: int = 5                # max dispatches per build iteration (bounds one pass)
    dispatch_role: str | None = None     # only inbox messages addressed to this role
    dispatch_session_budget: int = 50_000  # token budget for one pass (live bound; dummy=irrelevant)
    explicit_auth: bool = False          # user explicitly asked for a bounded "until done" loop
    goal: str | None = None              # optional /goal text; implies explicit_auth in CLI
    # internal: indirection for sleep so tests can monkey-patch
    sleeper: object = None


@dataclass
class IterationResult:
    iteration: int
    mode: str
    status: str  # "ok" | "skipped" | "failed" | "halted"
    detail: str = ""
    actions: list[str] = field(default_factory=list)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def ts_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).isoformat(timespec="seconds")


def date_today() -> str:
    return _dt.date.today().isoformat()


def append_event(event: str, **fields: object) -> None:
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EVENTS_DIR / f"agent_loop-{date_today()}.jsonl"
    record = {"ts": ts_now_iso(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_worktree_dirty() -> tuple[bool, str]:
    """Return (dirty, summary). Uses git porcelain status."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"git unavailable ({e!r}) — treating as clean"
    if result.returncode != 0:
        return False, f"git status exit {result.returncode}: {result.stderr.strip()}"
    output = result.stdout.strip()
    if not output:
        return False, "clean"
    lines = output.splitlines()
    return True, f"{len(lines)} change(s): {lines[0] if lines else ''}"


def write_heartbeat(cfg: LoopConfig, iteration: int, status: str,
                    failures: int = 0) -> None:
    """Write/refresh agents/runtime/heartbeat.json. Called between iterations
    so an external supervisor can detect a stalled loop.

    Disabled if cfg.heartbeat_interval == 0 or not (iteration % interval == 0).
    Tolerates write failures (heartbeat is best-effort, never blocks the loop).
    """
    if cfg.heartbeat_interval <= 0:
        return
    if iteration % cfg.heartbeat_interval != 0:
        return
    record = {
        "ts": ts_now_iso(),
        "iteration": iteration,
        "mode": cfg.mode,
        "status": status,            # "starting" | "iteration_done" | "iteration_error" | "stopped"
        "failures": failures,
        "max_iterations": cfg.max_iterations,
        "pid": _safe_pid(),
    }
    try:
        cfg.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.heartbeat_file.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _safe_pid() -> int:
    try:
        import os
        return os.getpid()
    except Exception:  # noqa: BLE001
        return -1


def apply_loop_safety_caps(cfg: LoopConfig) -> tuple[LoopConfig, list[str]]:
    """Normalize Ralph loop config so "until done" runs remain bounded.

    TASK-241 keeps the default safe one-shot behavior, but an explicit user
    "끝까지"/goal authorization can promote a run to a bounded multi-iteration
    loop. All caller-provided larger values are still clamped before execution.
    """
    capped = cfg
    notes: list[str] = []

    if capped.explicit_auth and capped.max_iterations <= 1:
        capped = replace(capped, max_iterations=EXPLICIT_AUTH_DEFAULT_ITERATIONS)
        notes.append(
            f"explicit_auth default: max_iterations={EXPLICIT_AUTH_DEFAULT_ITERATIONS}"
        )

    if capped.max_iterations > HARD_MAX_ITERATIONS:
        notes.append(
            f"max_iterations capped {capped.max_iterations}->{HARD_MAX_ITERATIONS}"
        )
        capped = replace(capped, max_iterations=HARD_MAX_ITERATIONS)

    if capped.dispatch_session_budget > HARD_DISPATCH_SESSION_BUDGET:
        notes.append(
            "dispatch_session_budget capped "
            f"{capped.dispatch_session_budget}->{HARD_DISPATCH_SESSION_BUDGET}"
        )
        capped = replace(capped, dispatch_session_budget=HARD_DISPATCH_SESSION_BUDGET)

    if capped.dispatch_max > HARD_DISPATCH_MAX:
        notes.append(f"dispatch_max capped {capped.dispatch_max}->{HARD_DISPATCH_MAX}")
        capped = replace(capped, dispatch_max=HARD_DISPATCH_MAX)

    return capped, notes


def stop_aware_sleep(cfg: LoopConfig, seconds: float) -> bool:
    """Sleep for `seconds`, polling for stop-file every 0.5s.

    Returns True if a stop file appeared during sleep; False otherwise.
    """
    if seconds <= 0:
        return cfg.stop_file.exists() or ORCHESTRATOR_STOP_FILE.exists()
    sleep_fn = cfg.sleeper if cfg.sleeper is not None else time.sleep
    remaining = seconds
    chunk = 0.5
    while remaining > 0:
        if cfg.stop_file.exists() or ORCHESTRATOR_STOP_FILE.exists():
            return True
        delta = chunk if remaining > chunk else remaining
        sleep_fn(delta)
        remaining -= delta
    return cfg.stop_file.exists() or ORCHESTRATOR_STOP_FILE.exists()


def backoff_seconds(failure_count: int, cap: int) -> float:
    """Exponential backoff: 2, 4, 8, 16... capped at `cap`. Returns 0 if cap<=0."""
    if cap <= 0 or failure_count <= 0:
        return 0.0
    return float(min(cap, 2 ** failure_count))


def check_stop_conditions(cfg: LoopConfig, iteration: int, failures: int) -> tuple[bool, str]:
    """Return (should_stop, reason). Called BEFORE each iteration body."""
    if iteration > cfg.max_iterations:
        return True, f"max_iterations reached ({cfg.max_iterations})"
    if failures >= cfg.max_failures:
        return True, f"max_failures reached ({cfg.max_failures})"
    if cfg.stop_file.exists():
        return True, f"stop file present: {_rel(cfg.stop_file)}"
    if ORCHESTRATOR_STOP_FILE.exists():
        return True, f"orchestrator emergency stop present: {_rel(ORCHESTRATOR_STOP_FILE)}"
    if not cfg.allow_dirty:
        dirty, summary = is_worktree_dirty()
        if dirty:
            return True, f"dirty worktree (use --allow-dirty to override): {summary}"
    return False, ""


def run_mode_plan(cfg: LoopConfig, iteration: int) -> IterationResult:
    """plan mode — read STATUS.md + propose next action.

    Dry-run: print which TASK is currently 진행 중 and what the next step would be.
    Non-dry-run: same as dry-run for first cut (planning is read-only by design).
    """
    actions: list[str] = []
    actions.append("READ agents/lead_engineer/STATUS.md (cycle pointer + 활성 작업)")
    actions.append("READ agents/lead_engineer/tasks/INDEX.md (TASK registry)")
    actions.append("PROPOSE next TASK based on priority order")
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status="ok",
        detail="plan mode is read-only — no state change; output preview only",
        actions=actions,
    )


def _run_build_dispatch(cfg: LoopConfig) -> str:
    """Run one bounded write-back auto_dispatch pass over the inbox (opt-in).

    Closes the Ralph loop (TASK-213): build mode can now actually drain pending
    inbox work using the runaway-proof runner (CYCLE-076/080) — synchronous,
    bounded by dispatch_max, stop-file aware, default dummy (no live spend unless
    DISPATCH_ENABLE_LIVE), and write_back so replies are written and originals
    marked answered. Never raises: any failure (incl. the live-gate SystemExit)
    is returned as a one-line string so build mode keeps its never-crash posture.
    """
    import io

    try:
        import auto_dispatch
        items = auto_dispatch.inbox_work_items(cfg.dispatch_role, limit=cfg.dispatch_max)
        if not items:
            return "dispatch: no open inbox work items"
        summary = auto_dispatch.run_bounded_dispatch(
            items, cfg.dispatch_provider,
            max_dispatches=cfg.dispatch_max,
            session_budget=cfg.dispatch_session_budget,
            write_back=True,
            out=io.StringIO(),
        )
        return (f"dispatch: provider={summary['provider']} "
                f"dispatched={summary['dispatched']} replied={summary['replied']} "
                f"halt={summary['halt_reason']} spent={summary['spent']}")
    except SystemExit as e:  # get_provider live-gate / unknown-provider refusal
        return f"dispatch: blocked ({e})"
    except Exception as e:  # never let an opt-in pass crash the loop
        return f"dispatch: error {e!r}"


def run_mode_build(cfg: LoopConfig, iteration: int) -> IterationResult:
    """build mode — claim next TASK + run worker.

    Dry-run: list which agent_orchestrator / agent_worker commands WOULD run.
    Non-dry-run: invoke agent_orchestrator /status as proof-of-life. With
        --dispatch, also run one bounded write-back auto_dispatch pass over the
        inbox (default off — auto-spawn stays deferred unless opted in).
    """
    actions: list[str] = []
    actions.append("CHECK orchestrator safety_gate.check_emergency_stop()")
    actions.append("CALL agent_orchestrator /status (read sessions + inbox)")
    actions.append("CALL agent_orchestrator /inbox <role> (next claimable message)")
    actions.append("SPAWN agent_worker --role <role> --provider claude-agent (commit gate)")
    if cfg.dispatch:
        actions.append(
            f"DISPATCH auto_dispatch(inbox, write_back=True, "
            f"provider={cfg.dispatch_provider}, max={cfg.dispatch_max})")
    if cfg.dry_run:
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="ok",
            detail="build mode dry-run: orchestrator+worker commands previewed (no exec)",
            actions=actions,
        )
    if ORCHESTRATOR_STOP_FILE.exists():
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="failed",
            detail=f"build mode: emergency stop present ({_rel(ORCHESTRATOR_STOP_FILE)}), aborting",
            actions=actions,
        )
    orch = REPO_ROOT / "scripts" / "agent_orchestrator.py"
    if not orch.exists():
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="failed",
            detail="build mode: scripts/agent_orchestrator.py not found",
            actions=actions,
        )
    try:
        result = subprocess.run(
            [sys.executable, str(orch), "/status"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="failed",
            detail=f"build mode: agent_orchestrator /status failed to run: {e!r}",
            actions=actions,
        )
    if result.returncode != 0:
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="failed",
            detail=f"build mode: agent_orchestrator /status exit {result.returncode}",
            actions=actions,
        )
    first_line = (result.stdout.strip().splitlines() or [""])[0]
    detail = (f"build mode non-dry-run: orchestrator /status OK. "
              f"first_line: {first_line[:160]}. ")
    if cfg.dispatch:
        detail += _run_build_dispatch(cfg) + "."
    else:
        detail += "worker spawn deferred (user opt-in via --dispatch or direct agent_worker invocation)."
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status="ok",
        detail=detail,
        actions=actions,
    )


PR_PATTERN = __import__("re").compile(r"\(#\d+\)\s*$")


def list_recent_merges(limit: int = 5) -> list[dict]:
    """Return recent merged PRs from git log. Each entry has sha, subject, files.

    Squash-merged PRs show as single commits with `(#NNN)` suffix; we match by
    subject pattern. Looks at the latest 50 commits to find up to `limit` PRs.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-50", "--pretty=format:%h|%s"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    entries: list[dict] = []
    for line in result.stdout.strip().splitlines():
        if "|" not in line:
            continue
        sha, subject = line.split("|", 1)
        if not PR_PATTERN.search(subject):
            continue
        try:
            files_result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", "--name-only",
                 "--pretty=format:", sha],
                capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            )
            files = [f for f in files_result.stdout.strip().splitlines() if f]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            files = []
        entries.append({"sha": sha, "subject": subject, "files": files})
        if len(entries) >= limit:
            break
    return entries


def risk_score(merge: dict) -> tuple[str, list[str]]:
    """Return (level, reasons). Heuristic risk eval based on paths and counts.

    Levels: low / medium / high. Pure function — no I/O.
    """
    files = merge.get("files", [])
    reasons: list[str] = []
    score = 0
    if len(files) >= 20:
        score += 2
        reasons.append(f"{len(files)} files changed (>=20)")
    elif len(files) >= 10:
        score += 1
        reasons.append(f"{len(files)} files changed (10-19)")
    sensitive_paths = ("scripts/orchestrator_safety_gate", "agents/messages/",
                       "scripts/providers/", "AGENTS.md", "CLAUDE.md",
                       ".github/workflows/", "package.json", "vercel.json")
    hits = [f for f in files if any(s in f for s in sensitive_paths)]
    if hits:
        score += 2
        reasons.append(f"touches sensitive: {hits[0]}" + (f" (+{len(hits)-1})" if len(hits) > 1 else ""))
    if any("test" in f.lower() for f in files):
        reasons.append("has test changes")
    else:
        if files:
            score += 1
            reasons.append("no test changes")
    level = "high" if score >= 3 else ("medium" if score >= 1 else "low")
    return level, reasons


def collab_gate_summary() -> list[str]:
    """Surface cycle_gate's required collaboration for the current diff (read-only).

    Closed-loop step (CYCLE-073): Ralph's review mode now reads cycle_gate so the
    loop tells the operator which worker `/call` roles and perspective subagents the
    current change requires — instead of the §16 policy sitting unused while Lead
    self-reviews. Best-effort: any git/import failure degrades to one 'unavailable'
    line and never raises, preserving review mode's never-crash posture.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import cycle_gate as cg
        changed = cg._git_changed("origin/main")
        if not changed:
            return ["  collab gate: origin/main 대비 변경 없음 — 작업 시작 후 재평가"]
        r = cg.evaluate(changed)
        subs = ", ".join(r["required_subagents"]) or "(없음 — Low)"
        workers = ", ".join(r.get("required_worker_roles") or []) or "(없음)"
        arts = ", ".join(r.get("required_artifacts") or [])
        return [
            f"  collab gate: 등급 {r['grade']} (변경 {len(changed)}개 파일)",
            f"    필수 subagent dispatch: {subs}",
            f"    필수 worker /call: {workers}",
            f"    필수 산출물: {arts}",
        ]
    except Exception as e:  # pragma: no cover - defensive; tested via patched raise
        return [f"  collab gate: 평가 불가 ({e.__class__.__name__}) — read-only, 무시 가능"]


def run_mode_review(cfg: LoopConfig, iteration: int) -> IterationResult:
    """review mode — list recent merges + risk eval + current-diff collab gate.

    Read-only. Outputs preview of merges, per-merge risk, and the cycle_gate
    collaboration requirements for the current diff. First cut: heuristic risk
    eval, no LLM call.
    """
    actions: list[str] = []
    actions.append("READ recent merges via git log (--merges -5)")
    actions.append("EVAL risk per merge (file count + sensitive paths + test presence)")
    actions.append("EVAL collaboration gate for current diff (cycle_gate)")
    actions.append("OUTPUT preview summary (no state change)")
    collab_lines = collab_gate_summary()
    merges = list_recent_merges(limit=5)
    if not merges:
        detail = ("review mode: no recent merges found (git unavailable or empty history)\n"
                  + "\n".join(collab_lines))
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="ok",
            detail=detail,
            actions=actions,
        )
    summary_lines = []
    for m in merges:
        level, reasons = risk_score(m)
        reasons_str = "; ".join(reasons) if reasons else "(no signals)"
        summary_lines.append(f"  - {m['sha']} [{level}] {m['subject']}")
        summary_lines.append(f"    reasons: {reasons_str}")
    detail = (f"review mode: {len(merges)} recent merge(s) scanned\n"
              + "\n".join(summary_lines) + "\n" + "\n".join(collab_lines))
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status="ok",
        detail=detail,
        actions=actions,
    )


def run_check_agent_docs() -> tuple[int, int, int, str]:
    """Run check_agent_docs.py and return (errors, warnings, infos, last_line)."""
    script = REPO_ROOT / "scripts" / "check_agent_docs.py"
    if not script.exists():
        return 0, 0, 0, "check_agent_docs.py not found"
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 0, 0, 0, f"check_agent_docs.py failed to run: {e!r}"
    output = result.stdout
    errors = sum(1 for line in output.splitlines() if line.startswith("ERROR:"))
    warnings = sum(1 for line in output.splitlines() if line.startswith("WARN:"))
    infos = sum(1 for line in output.splitlines() if line.startswith("INFO:"))
    last_line = (output.strip().splitlines() or [""])[-1]
    return errors, warnings, infos, last_line


def run_mode_audit(cfg: LoopConfig, iteration: int) -> IterationResult:
    """audit mode — run check_agent_docs.py + summarize lint state.

    Read-only. Runs the harness lint as subprocess and parses error/warn/info counts.
    First cut: no AUDIT-LOG diff or RUBRIC measurement — just lint summary.
    """
    actions: list[str] = []
    actions.append("RUN check_agent_docs.py (subprocess)")
    actions.append("PARSE ERROR / WARN / INFO counts")
    actions.append("OUTPUT lint summary + last status line")
    errors, warnings, infos, last_line = run_check_agent_docs()
    status_str = "clean" if errors == 0 and warnings == 0 else ("warn" if errors == 0 else "fail")
    detail = (f"audit mode: lint {status_str} — {errors} error(s) / {warnings} warning(s) / "
              f"{infos} info(s). last_line: {last_line}")
    result_status = "ok" if errors == 0 else "failed"
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status=result_status,
        detail=detail,
        actions=actions,
    )


def latest_retro_path() -> Path | None:
    """Find the most-recent RETRO-*.md by filename (YYYY-MM-DD sort)."""
    retro_dir = REPO_ROOT / "agents" / "lead_engineer" / "retros"
    if not retro_dir.exists():
        return None
    candidates = sorted(retro_dir.glob("RETRO-*.md"))
    return candidates[-1] if candidates else None


def parse_retro_forward(retro_path: Path) -> list[dict]:
    """Parse RETRO §5 Forward Actions table. Reuses TASK-068 helper if available.

    Returns list of {kind, proposal, priority, owner, source} dicts.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import promote_retro_forward  # type: ignore[import-not-found]
        items = promote_retro_forward.parse_forward_section(retro_path)
        return [
            {"kind": it.kind, "proposal": it.proposal, "priority": getattr(it, "priority", ""),
             "owner": getattr(it, "owner", ""), "source": getattr(it, "source", "")}
            for it in items
        ]
    except (ImportError, AttributeError, Exception):
        return []


def count_unregistered_tasks(items: list[dict]) -> int:
    """Of the TASK-kind items in items, count how many do NOT match an entry
    in tasks/INDEX.md by proposal substring. Heuristic — case-insensitive."""
    if not items:
        return 0
    index_path = REPO_ROOT / "agents" / "lead_engineer" / "tasks" / "INDEX.md"
    if not index_path.exists():
        return 0
    index_text = index_path.read_text(encoding="utf-8", errors="replace").lower()
    unregistered = 0
    for item in items:
        if "task" not in item.get("kind", "").lower():
            continue
        proposal = (item.get("proposal") or "").lower()
        if not proposal:
            continue
        keywords = [w for w in proposal.split() if len(w) >= 5][:3]
        if not keywords:
            continue
        if not any(kw in index_text for kw in keywords):
            unregistered += 1
    return unregistered


def run_mode_retro(cfg: LoopConfig, iteration: int) -> IterationResult:
    """retro mode — parse latest RETRO §5 + count TASK promotion gap.

    Read-only. First cut: detect unregistered TASK candidates from latest RETRO.
    Does NOT auto-register (user-approval gate per TASK-068).
    """
    actions: list[str] = []
    actions.append("FIND latest RETRO-*.md (filename sort)")
    actions.append("PARSE §5 Forward Actions via promote_retro_forward")
    actions.append("DETECT unregistered TASK candidates vs tasks/INDEX.md")
    actions.append("OUTPUT promotion gap (no auto-register, user gate)")
    retro = latest_retro_path()
    if retro is None:
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="ok",
            detail="retro mode: no RETRO-*.md found under agents/lead_engineer/retros/",
            actions=actions,
        )
    items = parse_retro_forward(retro)
    if not items:
        return IterationResult(
            iteration=iteration, mode=cfg.mode, status="ok",
            detail=f"retro mode: parsed {_rel(retro)} but §5 Forward Actions empty or missing",
            actions=actions,
        )
    task_items = [it for it in items if "task" in it.get("kind", "").lower()]
    unregistered = count_unregistered_tasks(items)
    detail = (f"retro mode: latest={_rel(retro)} — {len(items)} forward item(s), "
              f"{len(task_items)} TASK candidate(s), {unregistered} unregistered "
              f"(heuristic, user-approval gate before promote)")
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status="ok",
        detail=detail,
        actions=actions,
    )


def run_mode_not_implemented(cfg: LoopConfig, iteration: int) -> IterationResult:
    return IterationResult(
        iteration=iteration, mode=cfg.mode, status="skipped",
        detail=f"mode '{cfg.mode}' is scaffolded only — implementation pending",
        actions=[],
    )


MODE_HANDLERS = {
    "plan": run_mode_plan,
    "build": run_mode_build,
    "review": run_mode_review,
    "audit": run_mode_audit,
    "retro": run_mode_retro,
}


def render_iteration(result: IterationResult) -> str:
    lines = [
        f"[iteration {result.iteration}] mode={result.mode} status={result.status}",
        f"  detail: {result.detail}",
    ]
    if result.actions:
        lines.append("  actions (preview):")
        for action in result.actions:
            lines.append(f"    - {action}")
    return "\n".join(lines)


def run_loop(cfg: LoopConfig, out=None) -> int:
    cfg, cap_notes = apply_loop_safety_caps(cfg)
    out = out if out is not None else sys.stdout
    append_event("loop_start", mode=cfg.mode, max_iterations=cfg.max_iterations,
                 dry_run=cfg.dry_run, allow_dirty=cfg.allow_dirty,
                 heartbeat_interval=cfg.heartbeat_interval,
                 backoff_max_seconds=cfg.backoff_max_seconds,
                 dispatch_session_budget=cfg.dispatch_session_budget,
                 dispatch_max=cfg.dispatch_max,
                 explicit_auth=cfg.explicit_auth,
                 goal=cfg.goal,
                 safety_caps=cap_notes)
    out.write(f"agent_loop start: mode={cfg.mode} max_iterations={cfg.max_iterations} "
              f"dry_run={cfg.dry_run}\n")
    for note in cap_notes:
        out.write(f"  safety_cap: {note}\n")

    failures = 0
    iteration = 1
    while True:
        should_stop, reason = check_stop_conditions(cfg, iteration, failures)
        if should_stop:
            append_event("loop_stop", iteration=iteration, reason=reason)
            out.write(f"agent_loop stop: {reason}\n")
            write_heartbeat(cfg, iteration, "stopped", failures=failures)
            return 0

        write_heartbeat(cfg, iteration, "starting", failures=failures)
        handler = MODE_HANDLERS.get(cfg.mode)
        if handler is None:
            append_event("loop_error", iteration=iteration, error=f"unknown mode {cfg.mode}")
            out.write(f"ERROR: unknown mode {cfg.mode}\n")
            return 2

        try:
            result = handler(cfg, iteration)
        except Exception as e:  # noqa: BLE001
            failures += 1
            append_event("iteration_error", iteration=iteration, error=repr(e),
                         failures=failures)
            out.write(f"[iteration {iteration}] ERROR: {e!r} (failures={failures})\n")
            write_heartbeat(cfg, iteration, "iteration_error", failures=failures)
            wait = backoff_seconds(failures, cfg.backoff_max_seconds)
            if wait > 0:
                out.write(f"  backoff: sleeping {wait}s before retry (cap={cfg.backoff_max_seconds}s)\n")
                append_event("iteration_backoff", iteration=iteration, seconds=wait,
                             failures=failures)
                if stop_aware_sleep(cfg, wait):
                    append_event("loop_stop", iteration=iteration,
                                 reason="stop file during backoff sleep")
                    out.write(f"agent_loop stop: stop file detected during backoff\n")
                    write_heartbeat(cfg, iteration, "stopped", failures=failures)
                    return 0
            iteration += 1
            continue

        append_event("iteration_done", iteration=iteration, mode=result.mode,
                     status=result.status, actions_count=len(result.actions))
        out.write(render_iteration(result) + "\n")
        write_heartbeat(cfg, iteration, "iteration_done", failures=failures)
        if result.status == "failed":
            failures += 1
            if failures >= cfg.max_failures:
                append_event("loop_halt_max_failures", iteration=iteration,
                             failures=failures, max_failures=cfg.max_failures)
                out.write(f"agent_loop halt: max_failures reached "
                          f"({failures}/{cfg.max_failures}) — auto-stop\n")
                write_heartbeat(cfg, iteration, "stopped", failures=failures)
                return 1
            wait = backoff_seconds(failures, cfg.backoff_max_seconds)
            if wait > 0:
                out.write(f"  backoff: sleeping {wait}s before next iteration\n")
                append_event("iteration_backoff", iteration=iteration, seconds=wait,
                             failures=failures)
                if stop_aware_sleep(cfg, wait):
                    append_event("loop_stop", iteration=iteration,
                                 reason="stop file during backoff sleep")
                    out.write(f"agent_loop stop: stop file detected during backoff\n")
                    write_heartbeat(cfg, iteration, "stopped", failures=failures)
                    return 0
        iteration += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_loop",
        description="Ralph-style meta-loop runner (TASK-070). OS-independent.",
    )
    parser.add_argument("--mode", choices=VALID_MODES, required=True,
                        help=f"loop mode. Implemented: {IMPLEMENTED_MODES}")
    parser.add_argument("--max-iterations", type=int, default=1,
                        help="max iterations before auto-stop (default 1)")
    parser.add_argument("--max-failures", type=int, default=2,
                        help="max iteration failures before auto-stop (default 2)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        default=True,
                        help="preview actions without executing (default ON)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="execute actions for real (opt-in, build mode actually calls "
                             "agent_orchestrator /status; worker spawn still deferred)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="allow dirty git worktree (default: stop if dirty)")
    parser.add_argument("--stop-file", type=Path, default=STOP_FILE,
                        help=f"path to stop file (default: {_rel(STOP_FILE)})")
    parser.add_argument("--heartbeat-interval", type=int, default=1,
                        help="write heartbeat.json every N iterations (0 = disable, default 1)")
    parser.add_argument("--heartbeat-file", type=Path, default=HEARTBEAT_FILE,
                        help=f"path to heartbeat file (default: {_rel(HEARTBEAT_FILE)})")
    parser.add_argument("--backoff-max-seconds", type=int, default=0,
                        help="cap for exponential backoff on iteration failure (0 = disabled)")
    parser.add_argument("--dispatch", action="store_true",
                        help="build mode: after proof-of-life, run one bounded write-back "
                             "auto_dispatch pass over the inbox (default off; runaway-proof "
                             "runner, dummy unless DISPATCH_ENABLE_LIVE=1)")
    parser.add_argument("--dispatch-provider", default="dummy",
                        help="provider for --dispatch (dummy=safe default; live needs DISPATCH_ENABLE_LIVE=1)")
    parser.add_argument("--dispatch-max", type=int, default=5,
                        help="max dispatches per build iteration (bounds one --dispatch pass)")
    parser.add_argument("--dispatch-role", default=None,
                        help="with --dispatch, only inbox messages addressed to this role")
    parser.add_argument("--dispatch-session-budget", type=int, default=50_000,
                        help="token budget for one --dispatch pass (live bound; dummy ignores it)")
    parser.add_argument("--explicit-auth", action="store_true",
                        help="user explicitly authorized a bounded multi-iteration loop")
    parser.add_argument("--goal", default=None,
                        help="optional /goal text; implies --explicit-auth and remains hard-capped")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = LoopConfig(
        mode=args.mode,
        max_iterations=args.max_iterations,
        max_failures=args.max_failures,
        dry_run=args.dry_run,
        allow_dirty=args.allow_dirty,
        stop_file=args.stop_file,
        heartbeat_interval=args.heartbeat_interval,
        heartbeat_file=args.heartbeat_file,
        backoff_max_seconds=args.backoff_max_seconds,
        dispatch=args.dispatch,
        dispatch_provider=args.dispatch_provider,
        dispatch_max=args.dispatch_max,
        dispatch_role=args.dispatch_role,
        dispatch_session_budget=args.dispatch_session_budget,
        explicit_auth=bool(args.explicit_auth or args.goal),
        goal=args.goal,
    )
    return run_loop(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
