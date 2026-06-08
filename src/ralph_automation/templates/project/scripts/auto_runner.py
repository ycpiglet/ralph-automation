#!/usr/bin/env python3
"""auto_runner — 무인 자율 실행기 + Governor (TASK-225, Phase3, MEETING-2026-06-04-001).

스케줄 발화 시 적격(R1/R2) 작업을 골라(pick_eligible) 무인 실행→머지하되, 모든 행동을
동기 hard-block 인터셉터 **Governor** 가 먼저 통과시킨다. dry-run 이 기본이고 무인 실행
(--execute)은 R3 — Owner 가 켠다(무인 cron 발화 결선은 TASK-227, Owner 등록).

기존 primitive 재사용(병렬 안전기구 신설 금지, MEETING 안전설계):
  - kill-switch       : .auto-runner-stop / .orchestrator-stop / runtime/STOP_LOOP (3 체크포인트)
  - R3 double-gate    : grade(여기 task_grade_decision, fail-closed) AND path(auto_merge.r3_hits)
  - circuit-breaker   : N=2 연속 실패 → 정지
  - fail-closed 예산  : per-run cap, 비용 미상이면 deny
  - 머지              : auto_merge.evaluate() 게이트 경유(우회 없음)
  - 로깅              : 전 결정 events.jsonl + 위반/경고는 safety_violations(write_evidence)

Governor(실시간 집행) ≠ Independent Auditor(사후 심사) — 감사자는 동기 경로에 두지 않는다.

사용:
  python scripts/auto_runner.py                 # dry-run: 적격 작업 + 게이트 판정만(부작용 없음)
  python scripts/auto_runner.py --json          # 구조화 출력
  python scripts/auto_runner.py --execute        # R3: 적격 작업 PR 을 auto_merge 게이트로 머지(Owner)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator_safety_gate as osg  # SafetyDecision, write_evidence, check_emergency_stop
import auto_merge                       # r3_hits, evaluate, R3_PATTERNS (no-bypass merge gate)
import query_tasks                      # load_tasks
import schedule as schedule_mod         # read_schedules (TASK-227 routine 결선)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------- constants (기존 관례 재사용) ----------
STOP_FILES = [
    ROOT / ".auto-runner-stop",
    ROOT / ".orchestrator-stop",
    ROOT / "agents" / "runtime" / "STOP_LOOP",
]
EVENTS_DIR = ROOT / "agents" / "runtime" / "events"
CIRCUIT_BREAKER_N = 2
BUDGET_PER_RUN_DEFAULT = 40_000

# 무인 적격에서 배제하는 R3 신호(fail-closed):
R3_GATE_TOKENS = ("r3", "owner")  # frontmatter 'gate' 필드(소문자) 부분문자열
R3_TAGS = {"r3", "security", "Managed database", "secret", "migration",
           "deploy", "infra", "row-level policy", "auth"}
# 우선순위→예상 비용(budget_estimate.BASELINE_COST 와 정합). Critical 은 부적격이라 제외.
PRIORITY_COST = {"High": 40_000, "Medium": 25_000, "Low": 15_000}


# ---------- kill-switch (3 체크포인트가 호출) ----------

def kill_switch() -> str | None:
    """정지 신호가 있으면 사유 문자열, 없으면 None. 어느 체크포인트에서나 같은 함수."""
    for p in STOP_FILES:
        try:
            if p.exists():
                return f"kill-switch active ({p.name})"
        except Exception:
            continue
    return None


# ---------- R3 double-gate: grade half (fail-closed) ----------

def task_grade_decision(fm: dict) -> osg.SafetyDecision:
    """무인 적격은 '대기'·R1/R2 만. 모호하면 거부(fail-closed)."""
    if not fm:
        return osg.SafetyDecision.deny("no-frontmatter", "TASK frontmatter 없음 → fail-closed")
    if fm.get("status") != "대기":
        return osg.SafetyDecision.deny("not-pending", f"status={fm.get('status')} (대기 아님)")
    if (fm.get("priority") or "") == "Critical":
        return osg.SafetyDecision.deny("critical-audit", "Critical → audit 필요(Policy 7), 무인 부적격")
    gate = str(fm.get("gate") or "").lower()
    if any(tok in gate for tok in R3_GATE_TOKENS):
        return osg.SafetyDecision.deny("r3-gate", f"gate 필드 R3/Owner 신호: {fm.get('gate')!r}")
    tags = {str(t).lower() for t in (fm.get("tags") or [])}
    bad = tags & R3_TAGS
    if bad:
        return osg.SafetyDecision.deny("r3-tag", f"R3 태그: {sorted(bad)}")
    return osg.SafetyDecision.ok("eligible", "R1/R2 대기 — 무인 적격")


def pick_eligible(tasks: list[dict] | None = None) -> list[dict]:
    """task-picker (역할 아님 — auto_runner 내부 함수). 적격 TASK frontmatter 리스트."""
    if tasks is None:
        tasks = [fm for _p, fm in query_tasks.load_tasks()]
    return [fm for fm in tasks if task_grade_decision(fm).allowed]


# ---------- R3 double-gate: path half + fail-closed budget ----------

def merge_gate_decision(files: list[dict]) -> osg.SafetyDecision:
    """변경 파일이 R3 surface 면 거부. auto_merge.r3_hits 재사용(단일 R3 정의)."""
    hits = auto_merge.r3_hits(files)
    if hits:
        return osg.SafetyDecision.deny("r3-path", f"변경 파일 R3 surface: {hits}", matched=hits)
    return osg.SafetyDecision.ok("path-ok", "R3 surface 없음")


def budget_gate_decision(spent: int, cost, cap: int) -> osg.SafetyDecision:
    """fail-closed 예산. 비용 미상이면 거부."""
    if not isinstance(cost, int) or cost < 0:
        return osg.SafetyDecision.deny("budget-unknown", "비용 미상 → fail-closed")
    if spent + cost > cap:
        return osg.SafetyDecision.deny("budget-exceeded", f"{spent}+{cost} > cap {cap}")
    return osg.SafetyDecision.ok("budget-ok", f"{spent}+{cost} <= {cap}")


# ---------- Governor (상태 있는 동기 인터셉터) ----------

@dataclass
class Governor:
    per_run_budget: int = BUDGET_PER_RUN_DEFAULT
    circuit_n: int = CIRCUIT_BREAKER_N
    consecutive_failures: int = 0
    spent: int = 0
    events: list = field(default_factory=list)

    def tripped(self) -> bool:
        return self.consecutive_failures >= self.circuit_n

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def log(self, event: str, decision: osg.SafetyDecision, ctx: dict) -> dict:
        rec = {"event": event, "decision": decision.to_dict(), "ctx": ctx}
        self.events.append(rec)
        if decision.severity in ("warn", "error"):
            try:
                osg.write_evidence(decision, f"auto_runner:{event}", ctx)
            except Exception:
                pass
        return rec

    def flush_events(self) -> None:
        if not self.events:
            return
        try:
            EVENTS_DIR.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d")
            path = EVENTS_DIR / f"auto_runner-{day}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                for rec in self.events:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


def task_cost(fm: dict) -> int:
    return PRIORITY_COST.get(fm.get("priority", ""), 25_000)


# ---------- planner (dry-run, pure given tasks) ----------

def plan_run(execute: bool = False, gov: Governor | None = None,
             tasks: list[dict] | None = None) -> dict:
    """적격 작업에 Governor 게이트를 적용한 실행 계획. dry-run 에선 부작용 없음."""
    gov = gov or Governor()

    # checkpoint 1 — preflight kill-switch
    ks = kill_switch()
    if ks:
        gov.log("preflight", osg.SafetyDecision.deny("kill-switch", ks), {})
        gov.flush_events()
        return {"halted": True, "reason": ks, "decisions": []}

    eligible = pick_eligible(tasks)
    decisions: list[dict] = []
    for fm in eligible:
        tid = fm.get("id")
        # circuit-breaker
        if gov.tripped():
            decisions.append({"task": tid, "action": "halt", "reason": f"circuit-breaker N={gov.circuit_n}"})
            break
        # checkpoint 2 — per-task kill-switch
        ks = kill_switch()
        if ks:
            decisions.append({"task": tid, "action": "halt", "reason": ks})
            break
        # fail-closed budget
        cost = task_cost(fm)
        bg = budget_gate_decision(gov.spent, cost, gov.per_run_budget)
        if not bg.allowed:
            gov.log("budget", bg, {"task": tid, "cost": cost})
            decisions.append({"task": tid, "action": "skip", "reason": bg.reason})
            continue
        gov.spent += cost
        action = "execute(merge-gate)" if execute else "dry-run"
        gov.log("plan", osg.SafetyDecision.ok("planned", action), {"task": tid, "cost": cost})
        decisions.append({"task": tid, "action": action, "cost": cost})

    gov.flush_events()
    return {"halted": False, "eligible": [f.get("id") for f in eligible],
            "decisions": decisions, "spent": gov.spent}


# ---------- execute (R3 — Owner) ----------

def _open_prs() -> list[dict]:
    out = subprocess.run(["gh", "pr", "list", "--state", "open",
                          "--json", "number,headRefName"],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        return []
    try:
        return json.loads(out.stdout)
    except Exception:
        return []


def execute_run(gov: Governor | None = None) -> dict:
    """R3: 적격 작업의 PR(branch 에 TASK-NNN 포함)을 auto_merge 게이트로 머지.

    auto_merge 가 전 CI green·CLEAN·R3 surface 없음을 재확인하므로 우회 불가.
    무인 cron 발화 결선(TASK-227)은 별도 Owner 등록(settings) — 본 함수는 사람이 부른다.
    """
    gov = gov or Governor()
    ks = kill_switch()  # checkpoint 1
    if ks:
        return {"halted": True, "reason": ks, "merged": []}

    eligible_ids = {fm.get("id") for fm in pick_eligible(tasks=None)}
    prs = _open_prs()
    merged, skipped = [], []
    for pr in prs:
        if gov.tripped():
            break
        if kill_switch():  # checkpoint 2
            break
        branch = pr.get("headRefName", "")
        match = next((tid for tid in eligible_ids if tid and tid in branch), None)
        if not match:
            continue
        verdict, reasons, _ = auto_merge.evaluate(str(pr["number"]))  # checkpoint 3 (no-bypass)
        if verdict == "AUTO-MERGE":
            m = subprocess.run(["gh", "pr", "merge", str(pr["number"]), "--squash", "--delete-branch"],
                               capture_output=True, text=True, encoding="utf-8")
            ok = m.returncode == 0
            (merged if ok else skipped).append({"pr": pr["number"], "task": match, "ok": ok})
            gov.record_success() if ok else gov.record_failure()
        else:
            skipped.append({"pr": pr["number"], "task": match, "verdict": verdict, "reasons": reasons})
    gov.flush_events()
    return {"halted": False, "merged": merged, "skipped": skipped}


# ---------- schedule-driven dispatch (TASK-227 routine 결선) ----------

def resolve_schedule_action(entry: dict) -> dict:
    """SCHEDULE.yml 엔트리의 selector 를 무엇을 돌릴지로 해석(pure).

    maintenance/digest 는 읽기 전용(R1). task/tag 는 pick_eligible 가 R1/R2 만 거른다.
    """
    sel = str(entry.get("selector") or "")
    base = {"id": entry.get("id"), "selector": sel, "mode": entry.get("mode")}
    if sel == "maintenance":
        return {**base, "kind": "maintenance", "detail": "기존 due-check/doc 위생 실행(읽기 전용 R1)"}
    if sel == "digest":
        return {**base, "kind": "digest", "detail": "secretary digest 생성(R1)"}
    if sel.startswith("TASK-"):
        return {**base, "kind": "task", "detail": f"{sel} (Governor R3 double-gate 경유)"}
    return {**base, "kind": "tag", "detail": f"태그 '{sel}' 적격 작업(Governor 경유)"}


def from_schedule_plan() -> dict:
    """활성 스케줄을 읽어 각각 무엇을 발화할지 dry-run 으로 보고(routine 진입점)."""
    enabled = [s for s in schedule_mod.read_schedules() if s.get("enabled")]
    return {"enabled": len(enabled), "actions": [resolve_schedule_action(s) for s in enabled]}


def run_maintenance() -> dict:
    """maintenance selector 발화 시 기존 점검 스크립트를 실행(읽기 전용 R1, 신규 집계기 아님)."""
    checks = {
        "check_agent_docs": [str(ROOT / "scripts" / "check_agent_docs.py")],
        "doc_health": [str(ROOT / "scripts" / "doc_health_report.py"), "--json"],
        "scribe_due": [str(ROOT / "scripts" / "scribe_due.py"), "--quiet"],
        "doc_steward_due": [str(ROOT / "scripts" / "doc_steward_due.py"), "--quiet"],
    }
    results = {}
    for name, argv in checks.items():
        try:
            r = subprocess.run([sys.executable, *argv], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
            tail = (r.stdout or r.stderr or "").strip().splitlines()
            results[name] = {"rc": r.returncode, "tail": tail[-1] if tail else ""}
        except Exception as exc:
            results[name] = {"rc": -1, "error": str(exc)}
    return results


def run_digest() -> dict:
    """digest selector 발화 — secretary_digest 본문 생성(R1 읽기 전용, 파일 미작성)."""
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "secretary_digest.py"), "--stdout"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
        return {"rc": r.returncode, "body": (r.stdout or r.stderr or "").strip()}
    except Exception as exc:
        return {"rc": -1, "error": str(exc)}


# 로컬 notify 채널(per-machine, gitignore) — Task Scheduler/사람이 부른 발화 보고서 배달처.
SCHEDULE_RUNS_DIR = ROOT / "schedule_runs"


def _render_run_report(ran: list[dict]) -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = [f"# 자율 스케줄 발화 보고 — {ts}", "",
           f"활성 스케줄 {len(ran)}건 · 읽기 전용(R1) — 머지/배포 없음.", ""]
    for r in ran:
        out.append(f"## {r['id']} — {r['kind']}")
        if r.get("skipped"):
            out.append(f"- 스킵: {r['skipped']}")
        elif r["kind"] == "maintenance":
            for name, res in (r.get("result") or {}).items():
                tail = res.get("tail") or res.get("error") or ""
                out.append(f"- {name}: rc={res.get('rc')} {tail}")
        elif r["kind"] == "digest":
            res = r.get("result") or {}
            out.append(res.get("body") or f"(digest 실패: rc={res.get('rc')} {res.get('error', '')})")
        out.append("")
    return "\n".join(out)


def _write_run_report(report: str, report_dir: Path | None = None) -> Path:
    # 단일 파일(latest.md). 보고서는 현재 상태 스냅샷이라 일자별 사본은 중복이라 두지 않는다
    # (마지막 발화 시각은 파일 mtime = schedule_task 대시보드가 표시). 매 발화 덮어씀.
    d = report_dir or SCHEDULE_RUNS_DIR
    d.mkdir(parents=True, exist_ok=True)
    latest = d / "latest.md"
    latest.write_text(report, encoding="utf-8")
    return latest


def from_schedule_run(write_report: bool = True, report_dir: Path | None = None,
                      schedule_ids: set[str] | None = None) -> dict:
    """활성 notify 스케줄의 R1 작업(maintenance/digest)을 실제 실행하고 보고서를 배달.

    읽기 전용만 — 머지/R2/R3 없음. task/tag selector 는 여기서 절대 발화하지 않는다
    (머지 경로는 --execute=R3 전용). kill-switch 존중. 보고서를 로컬 notify 채널에 기록.
    """
    ks = kill_switch()  # checkpoint — 정지 신호면 부작용 0
    if ks:
        return {"halted": True, "reason": ks, "ran": []}
    enabled = [s for s in schedule_mod.read_schedules() if s.get("enabled")]
    if schedule_ids is not None:
        enabled = [s for s in enabled if str(s.get("id")) in schedule_ids]
    ran: list[dict] = []
    for entry in enabled:
        sel = str(entry.get("selector") or "")
        if sel == "maintenance":
            ran.append({"id": entry.get("id"), "kind": "maintenance", "result": run_maintenance()})
        elif sel == "digest":
            ran.append({"id": entry.get("id"), "kind": "digest", "result": run_digest()})
        else:
            ran.append({"id": entry.get("id"), "kind": sel,
                        "skipped": "task/tag selector 는 --execute(R3) 경로 — notify run 미발화"})
    report = _render_run_report(ran)
    report_path = _write_run_report(report, report_dir) if write_report else None
    return {"halted": False, "ran": ran, "report": report,
            "report_path": str(report_path) if report_path else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="무인 자율 실행기 + Governor (dry-run 기본)")
    ap.add_argument("--execute", action="store_true",
                    help="R3(Owner): 적격 작업 PR 을 auto_merge 게이트로 머지")
    ap.add_argument("--from-schedule", action="store_true",
                    help="활성 스케줄(SCHEDULE.yml)을 읽어 발화 계획 보고(routine 진입점, dry-run)")
    ap.add_argument("--run", action="store_true",
                    help="--from-schedule 와 함께: 활성 notify 스케줄의 R1 작업을 실제 실행+보고서 배달(읽기 전용)")
    ap.add_argument("--json", action="store_true", help="구조화 JSON 출력")
    args = ap.parse_args(argv)

    if args.from_schedule:
        if args.run:
            result = from_schedule_run()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif result.get("halted"):
                print(f"[auto_runner] 정지: {result['reason']}")
            else:
                print(f"[auto_runner] notify run — {len(result['ran'])}건 실행, 보고서: {result['report_path']}")
                for r in result["ran"]:
                    print(f"  {r['id']} ({r['kind']}): {r.get('skipped') or 'ok'}")
            return 0
        result = from_schedule_plan()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[auto_runner] 활성 스케줄 {result['enabled']}건")
            for a in result["actions"]:
                print(f"  {a['id']} (mode={a['mode']}) → {a['kind']}: {a['detail']}")
            print("  (무인 발화는 OS 스케줄러/사람이 --from-schedule --run 호출. SCHEDULE-ROUTINE.md 참조.)")
        return 0

    if args.execute:
        result = execute_run()
    else:
        result = plan_run(execute=False)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if result.get("halted"):
        print(f"[auto_runner] 정지: {result['reason']}")
        return 0
    if args.execute:
        print(f"[auto_runner] execute(R3) — 머지 {len(result['merged'])}건, 스킵 {len(result['skipped'])}건")
        for m in result["merged"]:
            print(f"  머지: PR #{m['pr']} ({m['task']})")
        for s in result["skipped"]:
            print(f"  스킵: PR #{s['pr']} ({s.get('task')}) — {s.get('verdict', 'merge 실패')}")
    else:
        print(f"[auto_runner] dry-run — 적격 {len(result['eligible'])}건, 계획 비용 ~{result['spent']} tok")
        for d in result["decisions"]:
            print(f"  {d['task']}: {d['action']}" + (f" — {d['reason']}" if d.get('reason') else ""))
        print("  (무인 실행은 --execute=R3·Owner. dry-run 은 부작용 없음.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
