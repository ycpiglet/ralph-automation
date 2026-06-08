#!/usr/bin/env python3
r"""schedule_task — OS 스케줄러(Windows Task Scheduler) 등록 CRUD + 대시보드 (TASK-227).

schedule.py(레지스트리 데이터 R2)와 auto_runner.py(실행)를 잇는 **OS 트리거 계층**.
schtasks 를 Python subprocess(리스트 인자, no-shell)로 호출 → **어느 셸에서 실행해도 동일**:
Git Bash 의 `/플래그`→경로 변환·`\` 이스케이프 문제를 통째로 회피한다(Owner 가 `!` 로
schtasks 를 돌렸을 때 깨진 원인). 비-Windows 에선 OS 조회를 graceful skip.

사용:
  python scripts/schedule_task.py status        # 대시보드(레지스트리 + OS 작업 + 최근 발화)
  python scripts/schedule_task.py register       # OS 작업 등록(매일 발화)
  python scripts/schedule_task.py register --time 07:53
  python scripts/schedule_task.py unregister      # OS 작업 해제
  python scripts/schedule_task.py run             # 지금 1회 발화(등록된 작업 실행)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import schedule as schedule_mod  # read_schedules (레지스트리)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TASK_NAME = "sample_project_Schedule"
WRAPPER = ROOT / "scripts" / "run_schedule_task.cmd"
REPORT = ROOT / "schedule_runs" / "latest.md"
DEFAULT_TIME = "07:53"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _ps(script: str, timeout: int = 12) -> tuple[int, str]:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except Exception as exc:
        return -1, str(exc)


def _schtasks(*args: str) -> dict:
    """schtasks 를 no-shell 로 호출(Git Bash 변환 회피). {ok, msg}."""
    if not _is_windows():
        return {"ok": False, "msg": "Windows 전용 — OS 작업은 Windows 에서만"}
    try:
        r = subprocess.run(["schtasks", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        return {"ok": r.returncode == 0, "msg": (r.stdout or r.stderr or "").strip()}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


def _psq(value: str) -> str:
    """Single-quote a PowerShell literal string."""
    return "'" + value.replace("'", "''") + "'"


# ---------- query ----------

def _ps_taskinfo(name: str = TASK_NAME) -> dict:
    """nextRun/lastResult/lastRun 을 locale 무관(객체 JSON)으로. best-effort.

    인라인 -Command 는 subprocess 인용이 셸별로 불안정 → 임시 .ps1 파일 + -File 로 회피.
    실패하면 빈 dict(상위에서 registered 는 schtasks 로 이미 확정)."""
    import os
    import tempfile
    script = (
        f"$i = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue | Get-ScheduledTaskInfo\n"
        "if ($i) { @{nextRun=[string]$i.NextRunTime; lastResult=$i.LastTaskResult; "
        "lastRun=[string]$i.LastRunTime} | ConvertTo-Json -Compress }\n"
    )
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            tmp = fh.name
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", tmp],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
        out = (r.stdout or "").strip()
        return json.loads(out) if out else {}
    except Exception:
        return {}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def query_os_task(name: str = TASK_NAME, details: bool = True) -> dict:
    """OS 작업 등록 여부 + (선택) 상태. 비-Windows 면 available=False.

    등록 여부는 `schtasks /Query`(System32, 어느 셸에서도 PATH) returncode 로 확정 —
    인라인 PowerShell 인용 불안정과 무관하게 robust. 상세(다음 발화·결과)는 details=True
    일 때만 PowerShell 로 enrich(없어도 등록 여부는 정확)."""
    if not _is_windows():
        return {"available": False, "registered": False, "reason": "Windows 전용"}
    q = _schtasks("/Query", "/TN", name)
    if not q["ok"]:
        return {"available": True, "registered": False}
    info = {"available": True, "registered": True}
    if details:
        info.update(_ps_taskinfo(name))
    return info


# ---------- CRUD (schtasks no-shell wrapper) ----------

def register(time_hhmm: str = DEFAULT_TIME, name: str = TASK_NAME) -> dict:
    """매일 time_hhmm 에 run_schedule_task.cmd 를 돌리는 OS 작업 등록(/F=덮어씀)."""
    if not _is_windows():
        return {"ok": False, "msg": "Windows 전용 — OS 작업은 Windows 에서만 등록"}
    if not WRAPPER.exists():
        return {"ok": False, "msg": f"래퍼 없음: {WRAPPER} (브랜치/머지 확인)"}
    comspec = os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
    action = f'{comspec} /d /c "{WRAPPER}"'
    created = _schtasks("/Create", "/TN", name, "/TR", action,
                        "/SC", "DAILY", "/ST", time_hhmm, "/F")
    if not created["ok"]:
        return created
    # schtasks 기본값은 노트북 배터리 상태에서 실행을 막는다. 이 repo의 notify
    # 스케줄은 R1 read-only라 배터리에서도 발화 가능하게 고정한다.
    action_arg = f'/d /c "{WRAPPER}"'
    ps = (
        f"$action = New-ScheduledTaskAction -Execute {_psq(comspec)} "
        f"-Argument {_psq(action_arg)} -WorkingDirectory {_psq(str(ROOT))}; "
        f"$settings = New-ScheduledTaskSettingsSet "
        f"-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-StartWhenAvailable -MultipleInstances IgnoreNew "
        f"-ExecutionTimeLimit (New-TimeSpan -Hours 72); "
        f"Set-ScheduledTask -TaskName {_psq(name)} -Action $action -Settings $settings | Out-Null; "
        f"'task settings ok'"
    )
    rc, out = _ps(ps, timeout=20)
    if rc != 0:
        return {"ok": False, "msg": f"{created['msg']}\n전원 설정 보강 실패: {out}"}
    return {"ok": True, "msg": f"{created['msg']}\n{out}"}


def unregister(name: str = TASK_NAME) -> dict:
    return _schtasks("/Delete", "/TN", name, "/F")


def run_now(name: str = TASK_NAME) -> dict:
    return _schtasks("/Run", "/TN", name)


# ---------- latest run summary ----------

def latest_summary(path: Path = REPORT) -> dict | None:
    """최근 발화 보고서의 mtime + Bottom Line 한 줄. 없으면 None."""
    if not path.exists():
        return None
    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    bl = ""
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("Bottom Line"):
            bl = ln.strip()
            break
    return {"mtime": mtime, "bottom_line": bl}


# ---------- 대시보드 렌더(pure — 테스트 대상) ----------

def _result_label(rc) -> str:
    if rc == 0:
        return "OK(0)"
    if rc is None:
        return "-"
    return f"코드 {rc}"


def render_board(os_task: dict, schedules: list[dict], latest: dict | None,
                 compact: bool = False) -> str:
    lines = ["[스케줄 대시보드] 자율 스케줄 + OS 트리거 상태"]

    if not os_task.get("available", True):
        lines.append("  OS 작업: (Windows 전용 — 이 OS 에선 조회 불가)")
    elif os_task.get("registered"):
        if os_task.get("nextRun"):
            detail = (f" · 다음 발화 {os_task['nextRun']} · 최근 결과 "
                      f"{_result_label(os_task.get('lastResult'))} · 최근 실행 {os_task.get('lastRun') or '-'}")
        else:
            detail = " (상세: schedule_task.py status)"
        lines.append(f"  OS 작업: 등록됨{detail}")
    else:
        lines.append("  OS 작업: 미등록 — 등록: python scripts/schedule_task.py register")

    on = [s for s in schedules if s.get("enabled")]
    lines.append(f"  레지스트리: 활성 {len(on)}/{len(schedules)}건")
    if not compact:
        for s in schedules:
            flag = "ON " if s.get("enabled") else "off"
            lines.append(f"    [{flag}] {str(s.get('id')):<20} {str(s.get('cron')):<14} "
                         f"→ {s.get('selector')} ({s.get('mode')})")

    if latest:
        lines.append(f"  최근 발화: {latest['mtime']} — {latest.get('bottom_line') or '(보고서 있음)'}")
    else:
        lines.append("  최근 발화: 아직 없음 (python scripts/auto_runner.py --from-schedule --run)")

    if not compact:
        lines.append("  관리: schedule_task.py [status|register|unregister|run] · "
                     "데이터: schedule.py [list|add|enable|disable|remove]")
    return "\n".join(lines)


def board(compact: bool = False) -> str:
    # compact(세션 시작 훅): PowerShell 상세 enrich 생략 — schtasks 등록여부만으로 빠르게.
    return render_board(query_os_task(details=not compact), schedule_mod.read_schedules(),
                        latest_summary(), compact=compact)


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OS 스케줄러 등록 CRUD + 대시보드 (cross-shell)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="대시보드(레지스트리 + OS 작업 + 최근 발화)")
    sub.add_parser("board", help="status 별칭")
    p_reg = sub.add_parser("register", help="OS 작업 등록(매일 발화)")
    p_reg.add_argument("--time", default=DEFAULT_TIME, help="HH:MM (기본 07:53)")
    sub.add_parser("unregister", help="OS 작업 해제")
    sub.add_parser("run", help="지금 1회 발화")
    args = ap.parse_args(argv)
    cmd = args.cmd or "status"

    if cmd in ("status", "board"):
        print(board())
        return 0
    if cmd == "register":
        res = register(args.time)
        print(("등록됨 — " if res["ok"] else "등록 실패: ") + res["msg"])
        if res["ok"]:
            print(board(compact=True))
        return 0 if res["ok"] else 1
    if cmd == "unregister":
        res = unregister()
        print(("해제됨 — " if res["ok"] else "해제 실패: ") + res["msg"])
        return 0 if res["ok"] else 1
    if cmd == "run":
        res = run_now()
        print(("발화함 — " if res["ok"] else "발화 실패: ") + res["msg"])
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
