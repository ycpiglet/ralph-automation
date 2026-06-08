"""auto_merge R3-surface 매칭 — 'secret' 오탐 회귀 방지 (PR #272에서 'secretary' 오탐 발견).

실 시크릿/비가역 surface 는 계속 R3 로 잡고, 'secretary'(secret 부분문자열)는 잡지 않는다.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_auto_merge", ROOT / "scripts" / "auto_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hits(am, *paths):
    return am.r3_hits([{"path": p} for p in paths])


def test_secretary_not_r3():
    am = _load()
    # "secretary" 는 secret 부분문자열이지만 시크릿이 아님 → R3 아님
    assert _hits(am, "agents/lead_engineer/tasks/TASK-226-secretary-digest.md") == []
    assert _hits(am, "scripts/secretary_digest.py") == []


def test_real_secret_surfaces_still_r3():
    am = _load()
    for f in [".env", ".env.local", "config/secret.json", "scripts/api_secret_key.py",
              "secrets/x", "Managed database/migrations/1.sql", ".github/workflows/test.yml",
              "scripts/migrate.py", "vercel.json"]:
        assert _hits(am, f), f"{f} 는 R3 로 잡혀야(시크릿/비가역 surface 보존)"


def test_plain_doc_not_r3():
    am = _load()
    assert _hits(am, "agents/lead_engineer/tasks/TASK-224-schedule-task-crud.md") == []
    assert _hits(am, "scripts/backlog_sweep.py") == []
