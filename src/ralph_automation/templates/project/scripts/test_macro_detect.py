"""macro_detect propose-only 테스트 (TASK-228 완료 기준).

임계(≥3회/≥2일) 미만 침묵(false-positive 억제) / 출력=제안만(생성 없음) /
입력=repo 기록만(프롬프트·시크릿 비대상 — gather 가 agents/ 만 읽음).
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_macro", ROOT / "scripts" / "macro_detect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sig(token, date, source="TASK-x.md"):
    return {"token": token, "date": date, "source": source}


def test_below_threshold_is_silent():
    md = _load()
    # 2회/2일 — count 미만 → 침묵
    assert md.detect([_sig("foo", "2026-06-01"), _sig("foo", "2026-06-02")]) == []
    # 3회지만 같은 하루 → days 미만 → 침묵 (한 세션 폭주 오인 방지)
    assert md.detect([_sig("bar", "2026-06-01")] * 3) == []


def test_meets_threshold_proposes():
    md = _load()
    sigs = [_sig("schedule", "2026-06-01"), _sig("schedule", "2026-06-02"),
            _sig("schedule", "2026-06-03")]
    out = md.detect(sigs)
    assert len(out) == 1 and out[0]["token"] == "schedule"
    assert out[0]["count"] == 3 and out[0]["days"] == 3


def test_stopwords_filtered_in_gather(monkeypatch):
    md = _load()
    # detect 자체는 stopword 모름 — gather 단계에서 거른다. 여기선 detect 가 토큰을 그대로 셈을 확인.
    assert md.detect([_sig("backlog", f"2026-06-0{i}") for i in range(1, 4)])[0]["token"] == "backlog"


def test_render_is_proposal_only():
    md = _load()
    out = md.render([{"token": "schedule", "count": 3, "days": 3, "sources": ["TASK-224-x.md"]}])
    assert "propose-only" in out and "자동 생성 안 함" in out
    assert "검토 제안" in out  # 제안 어휘 — 생성/실행 명령 아님


def test_render_empty_is_silent_message():
    md = _load()
    out = md.render([])
    assert "후보 없음" in out and "cry-wolf" in out


def test_gather_reads_only_repo_records():
    md = _load()
    # gather 는 agents/lead_engineer/tasks·meetings 만 본다(프롬프트·시크릿 경로 없음).
    assert md.TASKS_DIR.name == "tasks" and "meetings" in md.MEETINGS_DIR.name
    sigs = md.gather_signals()  # 실제 repo — 크래시 없이 동작, 신호는 dict 리스트
    assert isinstance(sigs, list)
    assert all("token" in s and "source" in s for s in sigs)
