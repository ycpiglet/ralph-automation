#!/usr/bin/env python3
"""Interactive live collaboration session launcher (TASK-127).

CEO 비전: "게임에서 몬스터/캐릭터를 소환해 싸우다 죽으면 사라지듯, 작업이 시작되면
화면이 분할돼 실시간으로 보고, 중간 개입하거나 종료 시 선택적으로 자동 종료되는
interactive 시스템."

이 런처는 기존 빌딩블록을 묶는다 (새 분할/관찰 로직 발명 없음):
  - 소환(분할)   : agent_terminal --split-mode (work pane + observe pane)
  - 관찰(실시간) : agent_console --watch (협업/세션/메시지/대기TASK 패널)
  - 자동 사라짐  : --exit-on-stop (stop-file 또는 작업 종료 시 관찰 pane 종료)
                   + agent_terminal --no-keep-open (pane 창 닫힘, TASK-109)
  - 중간 개입    : stop-file 토글 (`stop` 서브커맨드 → 양 pane 종료 = "죽으면 사라짐")

read-only 관찰 + stop-file 개입. 실제 visible 분할 실행은 preview-first 게이트
(사용자 승인 `launch --yes`). 순수 함수 build_session_plan 으로 레이아웃을 만들어
테스트 가능하게 한다.

CLI:
  python scripts/agent_live_session.py preview --observe console
  python scripts/agent_live_session.py preview --observe pipeline --pipeline build
  python scripts/agent_live_session.py launch --observe console --yes
  python scripts/agent_live_session.py stop      # 중간 개입 (stop-file 생성)
  python scripts/agent_live_session.py resume     # stop-file 제거
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "agents" / "runtime"
# 전용 stop-file — agent_loop 의 STOP_LOOP 와 분리해 Ralph 루프를 건드리지 않는다
# (TASK-127 reviewer 발견 #4). 본 런처의 stop/resume 은 라이브 세션 관찰 pane 만 제어.
STOP_FILE = RUNTIME_DIR / "STOP_LIVE_SESSION"
PY = sys.executable or "python"

OBSERVE_MODES = ("console", "pipeline")


@dataclass
class SessionPlan:
    """A live session = a work pane + an observe pane, with stop/exit policy."""
    observe: str                       # console | pipeline
    work_cmd: list[str]                # left pane (work)
    observe_cmd: list[str]             # right pane (observation, read-only)
    stop_file: Path
    exit_on_stop: bool = True
    split_mode: str = "pane-vertical"
    notes: list[str] = field(default_factory=list)


def build_session_plan(observe: str = "console", *,
                       work_cmd: list[str] | None = None,
                       pipeline: str = "build",
                       stop_file: Path = STOP_FILE,
                       exit_on_stop: bool = True,
                       width: int = 72) -> SessionPlan:
    """Pure: build the two-pane live-session plan (no side effects)."""
    if observe not in OBSERVE_MODES:
        raise ValueError(f"observe must be one of {OBSERVE_MODES}, got '{observe}'")

    # 좌: 작업 pane. 기본은 셸(사용자가 직접 작업/협업 명령 실행). 지정 시 그 명령.
    work = work_cmd or ["python", "scripts/agent_console.py", "--once"]

    # 우: 관찰 pane (read-only, 실시간 watch).
    if observe == "console":
        observe_cmd = [PY, "scripts/agent_console.py", "--watch",
                       "--width", str(width)]
        if exit_on_stop:
            observe_cmd += ["--exit-on-stop", "--stop-file", str(stop_file)]
    else:  # pipeline
        observe_cmd = [PY, "scripts/agent_observer.py", "--pipeline", pipeline]
        if exit_on_stop:
            observe_cmd += ["--exit-on-stop"]

    # pipeline observe pane(agent_observer --exit-on-stop)은 파이프라인 terminal
    # 상태에서 종료 — 본 런처의 stop-file 은 console observe pane 만 제어한다
    # (TASK-127 reviewer 발견 #4 정직 반영).
    intervene = (
        f"중간 개입: `agent_live_session.py stop` → {stop_file.name} 생성 → "
        + ("console observe pane 종료" if observe == "console"
           else "console 모드만 stop-file 반응 (pipeline 은 파이프라인 종료 시 자동 exit)")
    )
    notes = [
        "소환: work pane + observe pane 분할",
        "관찰: observe pane 가 --watch 로 실시간 렌더 (read-only)",
        ("자동 사라짐: exit-on-stop ON — stop-file 또는 작업 종료 시 관찰 종료"
         if exit_on_stop else "자동 사라짐: OFF (관찰 pane 유지)"),
        intervene,
    ]
    return SessionPlan(observe=observe, work_cmd=work, observe_cmd=observe_cmd,
                       stop_file=stop_file, exit_on_stop=exit_on_stop,
                       split_mode="pane-vertical", notes=notes)


def render_plan(plan: SessionPlan) -> str:
    """Pure: human-readable preview of the session plan."""
    lines = [
        "═══ LIVE SESSION PLAN (TASK-127) ═══",
        f"observe mode : {plan.observe}",
        f"split        : {plan.split_mode}",
        f"exit-on-stop : {plan.exit_on_stop}",
        f"stop-file    : {_rel(plan.stop_file)}",
        "",
        f"  [work pane ] {' '.join(plan.work_cmd)}",
        f"  [observe   ] {' '.join(plan.observe_cmd)}",
        "",
        "흐름:",
    ]
    lines += [f"  - {n}" for n in plan.notes]
    return "\n".join(lines) + "\n"


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def launch_via_terminal(plan: SessionPlan, yes: bool = False) -> int:
    """Launch the split session by delegating to agent_terminal (visible panes).

    preview-first 게이트: yes=False 면 명령만 출력하고 실행 안 함.
    """
    # observe pane 을 agent_terminal 의 split 으로 띄운다. work pane 은 현재 셸.
    # agent_terminal --command 는 nargs=REMAINDER 라 *맨 끝* 이어야 한다 — 그 뒤의
    # 플래그(--yes 등)는 command 로 삼켜진다 (TASK-127 reviewer 발견 #3). 따라서
    # --yes 를 --command 앞에 두고, observe_cmd 는 분리 토큰으로 넘긴다.
    argv = [PY, "scripts/agent_terminal.py",
            "launch" if yes else "preview",
            "--adapter", "auto",
            "--split-mode", plan.split_mode]
    if yes:
        argv.append("--yes")
    argv += ["--command", *plan.observe_cmd]
    print(f"[live-session] {'launching' if yes else 'preview'}: {' '.join(argv)}")
    try:
        return subprocess.run(argv, cwd=str(ROOT)).returncode
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


# ---------- CLI ----------


def _cmd_preview(args: argparse.Namespace) -> int:
    plan = build_session_plan(args.observe, pipeline=args.pipeline,
                              exit_on_stop=not args.no_exit_on_stop,
                              width=args.width)
    sys.stdout.write(render_plan(plan))
    return 0


def _cmd_launch(args: argparse.Namespace) -> int:
    plan = build_session_plan(args.observe, pipeline=args.pipeline,
                              exit_on_stop=not args.no_exit_on_stop,
                              width=args.width)
    sys.stdout.write(render_plan(plan))
    return launch_via_terminal(plan, yes=args.yes)


def _cmd_stop(args: argparse.Namespace) -> int:
    """중간 개입 — stop-file 생성. exit-on-stop 관찰/워커가 종료된다."""
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop requested by agent_live_session\n", encoding="utf-8")
    print(f"[live-session] stop-file 생성: {_rel(STOP_FILE)} — exit-on-stop pane 종료됨")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    if STOP_FILE.exists():
        STOP_FILE.unlink()
        print(f"[live-session] stop-file 제거: {_rel(STOP_FILE)}")
    else:
        print("[live-session] stop-file 없음 (이미 resume 상태)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_live_session.py",
        description="Interactive live collaboration session launcher (TASK-127).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    for verb, fn in (("preview", _cmd_preview), ("launch", _cmd_launch)):
        sp = sub.add_parser(verb, help=f"{verb} a split live session")
        sp.add_argument("--observe", choices=OBSERVE_MODES, default="console")
        sp.add_argument("--pipeline", default="build", help="pipeline name (observe=pipeline)")
        sp.add_argument("--width", type=int, default=72)
        sp.add_argument("--no-exit-on-stop", action="store_true",
                        help="관찰 pane 자동 종료 비활성 (기본은 ON)")
        if verb == "launch":
            sp.add_argument("--yes", action="store_true",
                            help="실제 visible 분할 실행 (게이트)")
        sp.set_defaults(func=fn)

    sub.add_parser("stop", help="중간 개입 — stop-file 생성 (pane 종료)").set_defaults(func=_cmd_stop)
    sub.add_parser("resume", help="stop-file 제거").set_defaults(func=_cmd_resume)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
