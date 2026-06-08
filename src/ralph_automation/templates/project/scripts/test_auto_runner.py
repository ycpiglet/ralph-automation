"""auto_runner Governor 안전 테스트 (TASK-225 완료 기준).

dry-run 이 R1/R2 적격만 선별하고 R3-surface 를 절대 미선별 / kill-switch 3 체크포인트 /
circuit-breaker N=2 / fail-closed 예산 / 머지는 auto_merge.r3_hits 경유(우회 없음).
모두 부작용 없는 순수 로직 — 실제 gh/merge 는 호출하지 않는다.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_auto_runner", ROOT / "scripts" / "auto_runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass(Governor) 가 sys.modules[cls.__module__] 를 참조
    spec.loader.exec_module(mod)
    return mod


def _fm(**kw):
    base = {"id": "TASK-900", "status": "대기", "priority": "Medium", "tags": [], "gate": ""}
    base.update(kw)
    return base


# ---- pick_eligible / grade-gate (fail-closed) ----

def test_eligible_only_r1_r2_pending():
    ar = _load()
    tasks = [
        _fm(id="TASK-901", status="대기", priority="High"),       # eligible
        _fm(id="TASK-902", status="대기", priority="Medium"),     # eligible
        _fm(id="TASK-903", status="완료"),                        # not pending
        _fm(id="TASK-904", status="대기", priority="Critical"),   # critical → audit
        _fm(id="TASK-905", status="대기", gate="무인 실행은 R3 — Owner"),  # R3 gate
        _fm(id="TASK-906", status="대기", tags=["security", "auth"]),     # R3 tag
    ]
    ids = [fm["id"] for fm in ar.pick_eligible(tasks)]
    assert ids == ["TASK-901", "TASK-902"]


def test_grade_gate_fail_closed_on_missing_frontmatter():
    ar = _load()
    assert ar.task_grade_decision({}).allowed is False
    assert ar.task_grade_decision({"status": "대기"}).allowed is True  # minimal valid


def test_no_r3_surface_task_ever_selected():
    ar = _load()
    # 모든 R3 신호 조합은 배제돼야
    for bad in (_fm(priority="Critical"), _fm(gate="R3"), _fm(gate="Owner 결정"),
                _fm(tags=["Managed database"]), _fm(tags=["migration"]), _fm(status="진행 중")):
        assert ar.pick_eligible([bad]) == []


# ---- merge path-gate (auto_merge.r3_hits 재사용) ----

def test_merge_gate_blocks_r3_paths():
    ar = _load()
    assert ar.merge_gate_decision([{"path": ".env"}]).allowed is False
    assert ar.merge_gate_decision([{"path": "Managed database/x.sql"}]).allowed is False
    assert ar.merge_gate_decision([{"path": "scripts/foo.py"}]).allowed is True
    # secretary 오탐 회귀 가드(merge gate 도 같은 정의 공유)
    assert ar.merge_gate_decision([{"path": "scripts/secretary_digest.py"}]).allowed is True


# ---- fail-closed budget ----

def test_budget_fail_closed():
    ar = _load()
    assert ar.budget_gate_decision(0, None, 40000).allowed is False     # cost unknown
    assert ar.budget_gate_decision(0, "lots", 40000).allowed is False   # non-int
    assert ar.budget_gate_decision(30000, 20000, 40000).allowed is False  # over cap
    assert ar.budget_gate_decision(0, 25000, 40000).allowed is True


# ---- circuit-breaker N=2 ----

def test_circuit_breaker_trips_at_two():
    ar = _load()
    gov = ar.Governor()
    assert gov.tripped() is False
    gov.record_failure()
    assert gov.tripped() is False
    gov.record_failure()
    assert gov.tripped() is True
    gov.record_success()  # 성공이 리셋
    assert gov.tripped() is False


# ---- kill-switch (3 체크포인트가 호출하는 단일 함수) ----

def test_kill_switch_detects_stop_file(tmp_path, monkeypatch):
    ar = _load()
    stop = tmp_path / ".auto-runner-stop"
    monkeypatch.setattr(ar, "STOP_FILES", [stop])
    assert ar.kill_switch() is None
    stop.write_text("stop", encoding="utf-8")
    assert ar.kill_switch() is not None


def test_plan_run_halts_on_kill_switch(tmp_path, monkeypatch):
    ar = _load()
    stop = tmp_path / ".orchestrator-stop"
    stop.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ar, "STOP_FILES", [stop])
    result = ar.plan_run(tasks=[_fm()])
    assert result["halted"] is True
    assert result["decisions"] == []


def test_plan_run_budget_caps_eligible(tmp_path, monkeypatch):
    ar = _load()
    monkeypatch.setattr(ar, "STOP_FILES", [tmp_path / "none"])  # no kill switch
    monkeypatch.setattr(ar, "EVENTS_DIR", tmp_path / "events")
    tasks = [_fm(id=f"TASK-9{i:02d}", priority="High") for i in range(5)]  # 40k each
    gov = ar.Governor(per_run_budget=40_000)
    result = ar.plan_run(tasks=tasks, gov=gov)
    # 첫 작업만 예산 안에 들어가고 나머지는 skip-budget
    planned = [d for d in result["decisions"] if d["action"] == "dry-run"]
    assert len(planned) == 1
    assert any(d["action"] == "skip" for d in result["decisions"])
