#!/usr/bin/env python3
"""SessionStart hook — multi-agent collaboration cockpit (TASK-204, CYCLE-072).

The repeat problem: the role machinery (subagent dispatch, retro, seminar, Ralph,
due-checks) existed only as scripts nobody auto-ran, so Lead self-did everything.
This hook fires every session and surfaces, in one compact block:
  - the collaboration the *current diff* requires (cycle_gate),
  - which dormant roles are due (doc_steward/beta/scribe due-checks),
  - the exact commands to invoke the self-improvement APIs.

Output goes to stdout → injected as session context. Always exits 0 (advisory,
never blocks a session). Best-effort: any sub-check failure is swallowed.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:  # Windows 콘솔 cp949 에서도 한글 stdout 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.check_output(
            cmd, cwd=str(REPO_ROOT), text=True, encoding="utf-8", errors="replace",
            stderr=subprocess.DEVNULL, timeout=timeout,
        ).strip()
    except Exception:
        return ""


def _git_behind() -> str:
    """origin/main 이 로컬보다 앞서면 게시판이 stale 일 수 있음 — read-only best-effort.

    절대 pull/merge 하지 않는다(머지는 auto_merge 게이트 전용). fetch 실패(offline/auth)는
    조용히 무시하고 캐시된 ref 로 비교.
    """
    _run(["git", "fetch", "--quiet", "origin", "main"], timeout=8)
    n = _run(["git", "rev-list", "--count", "HEAD..origin/main"], timeout=8)
    if n.isdigit() and int(n) > 0:
        return f"  ⚠ origin/main 이 {n} commit 앞섬 — 로컬 게시판 stale 가능. `git pull` 로 최신화 후 보기."
    return ""


def _backlog_board() -> list[str]:
    """BACKLOG.md(열린 작업 단일 포인터)를 세션 컨텍스트에 주입 — 읽기를 불가피하게.

    근거 AUDIT-2026-06-04-002: 다른 세션/PC/에이전트가 항상 같은 게시판을 보게 한다.
    """
    backlog = REPO_ROOT / "agents" / "lead_engineer" / "tasks" / "BACKLOG.md"
    out = ["[복도 게시판] 열린 작업 단일 포인터 = agents/lead_engineer/tasks/BACKLOG.md"]
    behind = _git_behind()
    if behind:
        out.append(behind)
    if not backlog.exists():
        out.append("  (BACKLOG.md 없음 — `python scripts/generate_views.py` 실행)")
        return out
    text = backlog.read_text(encoding="utf-8")
    idx = text.find("\n## ")  # 헤더 주석 블록 건너뛰고 섹션(진행중/대기/보류)부터
    body = text[idx:].strip() if idx != -1 else text
    # 세션 시작 컨텍스트 비용 방어: 과도하게 길면 자른다
    lines = body.splitlines()
    if len(lines) > 60:
        lines = lines[:60] + ["  … (생략 — 전체는 BACKLOG.md)"]
    out.extend("  " + ln if ln.strip() else ln for ln in lines)
    out.append("  → 새 작업은 산문/메모리 아닌 TASK 로 등록해야 게시판에 반영(COMPOUND-031/032).")
    return out


def _schedule_board() -> list[str]:
    """자율 스케줄 + OS 트리거 상태를 compact 로 주입(백로그처럼 한눈에). best-effort."""
    try:
        import schedule_task as st
        lines = ["", st.board(compact=True)]
        try:
            import local_schedule_daemon as lsd
            lines.append(lsd.render_status())
        except Exception:
            pass
        return lines
    except Exception:
        return []


def main() -> int:
    # 복도 게시판 먼저(가장 위) — 어느 세션/PC 든 같은 최신 열린작업에 수렴
    lines = _backlog_board()
    # 스케줄 대시보드(OS 작업 등록·다음 발화·최근 발화 한눈에)
    lines.extend(_schedule_board())
    lines.append("")
    lines.append("[협업 콕핏] 멀티에이전트 머신을 Lead 가 독점하지 않도록 — 이번 작업에 필요한 호출:")

    # 1) what the current diff requires
    try:
        import cycle_gate as cg
        changed = cg._git_changed("origin/main")
        if changed:
            r = cg.evaluate(changed)
            subs = ", ".join(r["required_subagents"]) or "(없음 — Low)"
            workers = ", ".join(r.get("required_worker_roles") or []) or "(없음)"
            lines.append(f"  • cycle_gate: 등급 {r['grade']} → 필수 subagent dispatch: {subs}")
            lines.append(f"    필수 worker /call: {workers}")
            lines.append(f"    실 변경 {len(changed)}개 파일 기준. 상세: python scripts/cycle_gate.py --diff origin/main")
        else:
            lines.append("  • cycle_gate: origin/main 대비 변경 없음 — 작업 시작 후 다시 평가")
    except Exception:
        lines.append("  • cycle_gate: (평가 생략)")

    # 2) dormant-role due-checks
    due = []
    for role, script in (("doc_steward", "doc_steward_due.py"),
                         ("beta_tester", "beta_tester_due.py"),
                         ("scribe", "scribe_due.py")):
        out = _run([sys.executable, f"scripts/{script}"])
        first = out.splitlines()[0] if out else ""
        if first and ("due" in first or "overdue" in first):
            due.append(f"{role}({'overdue' if 'overdue' in first else 'due'})")
    if due:
        lines.append(f"  • 휴면 역할 활성 필요: {', '.join(due)} — 해당 due-check 실행")

    # 3) self-improvement / collaboration API quick-ref
    lines.append("  • 자가개선 API: /retro(agent_retro run --all) · /seminar(agent_seminar, T2/T3 결정) · "
                 "council(subagent_council) · feed-forward(promote_retro_forward) · Ralph(agent_loop)")
    lines.append("  • 원칙(AGENTS §16): Critical=council(reviewer+auditor+skeptic) / High=reviewer+skeptic / "
                 "Medium=reviewer. 단일 세션도 subagent 호출이 self-review 를 *대체하지 않음*(§15.7).")

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
