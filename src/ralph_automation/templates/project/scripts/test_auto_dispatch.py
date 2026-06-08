"""Unit tests for the bounded synchronous auto-dispatch runner (TASK-208).

These assert the anti-runaway invariants by construction: every halt condition
(work_exhausted / max_dispatches / stop_file / session_budget) fires before a
billable call, budget accounting is synchronous and monotonic, and one bad
dispatch is captured rather than aborting the run or orphaning. All providers
are dummy or fakes — no live token spend, so this is CI-safe.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_dispatch  # noqa: E402
from auto_dispatch import SessionBudget, run_bounded_dispatch  # noqa: E402


class _FakeResult:
    def __init__(self, tokens_in=0, tokens_out=0, finish_reason="stop", error=None):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.finish_reason = finish_reason
        self.error = error


class _FakeProvider:
    """Records every run() call and returns a fixed per-call token cost."""

    def __init__(self, tokens_per_call=10, raise_on=None, model=None):
        self.tokens_per_call = tokens_per_call
        self.raise_on = raise_on  # index that should raise
        self.model = model
        self.calls = []

    def run(self, role, instruction, context):
        idx = len(self.calls)
        self.calls.append((role, instruction, context))
        if self.raise_on is not None and idx == self.raise_on:
            raise RuntimeError("boom")
        return _FakeResult(tokens_in=self.tokens_per_call, tokens_out=0)


@pytest.fixture
def patch_provider(monkeypatch):
    """Swap get_provider for a fake so budget/error paths are deterministic and
    never touch a live backend."""
    def _install(provider):
        monkeypatch.setattr(auto_dispatch, "get_provider", lambda name: provider)
        return provider
    return _install


def _items(n):
    return [{"role": "worker", "instruction": f"t{i}"} for i in range(n)]


def _run(items, provider, **kw):
    kw.setdefault("out", io.StringIO())
    return run_bounded_dispatch(items, "fake", **kw)


# ---- SessionBudget ----

def test_budget_remaining_never_negative():
    b = SessionBudget(total=100)
    b.record(150)
    assert b.remaining() == 0
    assert b.exhausted() is True


def test_budget_record_is_monotonic_and_clamps_negative():
    b = SessionBudget(total=100)
    b.record(30)
    b.record(-50)  # defensive clamp — spend cannot decrease
    assert b.spent == 30


# ---- halt conditions ----

def test_halts_on_work_exhausted(patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=1))
    summary = _run(_items(3), p, session_budget=1000, max_dispatches=10)
    assert summary["halt_reason"] == "work_exhausted"
    assert summary["dispatched"] == 3
    assert len(p.calls) == 3


def test_halts_on_max_dispatches_before_billing(patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=1))
    summary = _run(_items(10), p, session_budget=10_000, max_dispatches=4)
    assert summary["halt_reason"] == "max_dispatches (4)"
    assert summary["dispatched"] == 4
    assert len(p.calls) == 4  # never dispatched the 5th


def test_halts_on_session_budget_blocks_next_dispatch(patch_provider):
    # Hard ceiling: if the next dispatch cannot fit in the remaining session
    # budget, it is skipped before the provider is called.
    p = patch_provider(_FakeProvider(tokens_per_call=40))
    summary = _run(_items(10), p, session_budget=100, max_dispatches=10)
    assert summary["halt_reason"] == "session_budget (100)"
    assert summary["spent"] == 80
    assert len(p.calls) == 2
    assert summary["results"][-1]["finish_reason"] == "skipped"
    assert summary["results"][-1]["error"] == "budget_insufficient"


def test_session_budget_caps_provider_per_dispatch_before_call(patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=80_000))
    summary = _run(_items(1), p, session_budget=50_000, max_dispatches=10)

    assert summary["halt_reason"] == "session_budget (50000)"
    assert summary["spent"] == 0
    assert len(p.calls) == 0
    assert summary["results"][0]["finish_reason"] == "skipped"
    assert summary["results"][0]["error"] == "budget_insufficient"


def test_halts_on_stop_file(tmp_path, patch_provider):
    stop = tmp_path / "STOP_LOOP"
    stop.write_text("halt")
    p = patch_provider(_FakeProvider(tokens_per_call=1))
    summary = _run(_items(5), p, max_dispatches=10, stop_files=(stop,))
    assert summary["halt_reason"] == f"stop_file ({stop.name})"
    assert summary["dispatched"] == 0
    assert len(p.calls) == 0  # not a single billable call once stop present


# ---- error capture ----

def test_provider_error_is_captured_not_raised(patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=10, raise_on=1))
    summary = _run(_items(3), p, session_budget=10_000, max_dispatches=10)
    # all three still attempted; accounting not aborted by the middle failure
    assert summary["dispatched"] == 3
    bad = summary["results"][1]
    assert bad["finish_reason"] == "error"
    assert "RuntimeError" in bad["error"]
    assert bad["tokens"] == 0
    # spend reflects only the two good calls
    assert summary["spent"] == 20


def test_summary_token_total_sums_in_and_out(patch_provider):
    class _BothProvider(_FakeProvider):
        def run(self, role, instruction, context):
            self.calls.append((role, instruction, context))
            return _FakeResult(tokens_in=7, tokens_out=5)

    p = patch_provider(_BothProvider())
    summary = _run(_items(2), p, session_budget=10_000, max_dispatches=10)
    assert summary["spent"] == 24  # (7+5) * 2
    assert summary["results"][0]["tokens"] == 12


# ---- live gate (real get_provider, no monkeypatch) ----

def test_live_provider_blocked_without_env(monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    with pytest.raises(SystemExit):
        run_bounded_dispatch(_items(1), "claude", out=io.StringIO())


def test_dummy_provider_runs_without_env(monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    summary = run_bounded_dispatch(_items(2), "dummy", max_dispatches=10,
                                   out=io.StringIO())
    assert summary["dispatched"] == 2
    assert summary["halt_reason"] == "work_exhausted"


def test_unknown_provider_raises():
    with pytest.raises(SystemExit):
        run_bounded_dispatch(_items(1), "no_such_provider", out=io.StringIO())


def test_empty_work_list_no_dispatch(patch_provider):
    p = patch_provider(_FakeProvider())
    summary = _run([], p, session_budget=1000, max_dispatches=10)
    assert summary["dispatched"] == 0
    assert summary["spent"] == 0
    assert summary["halt_reason"] == "work_exhausted"
    assert p.calls == []


# ---- inbox work-source adapter (TASK-210) ----

def _write_msg(inbox, name, *, to, status, mtype="question", body="do the thing",
               routing_model=None, routing_grade=None):
    routing_lines = ""
    if routing_model:
        routing_lines += f"routing_model: {routing_model}\n"
    if routing_grade:
        routing_lines += f"routing_grade: {routing_grade}\n"
    msg = inbox / name
    msg.write_text(
        "---\n"
        f"id: {name[:-3]}\n"
        "from: backend\n"
        f"to: {to}\n"
        f"type: {mtype}\n"
        f"status: {status}\n"
        "ts: 2026-06-03T07:00:00+09:00\n"
        f"{routing_lines}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return msg


def test_inbox_work_items_selects_open_non_reply(tmp_path):
    _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    _write_msg(tmp_path, "MSG-20260603-070001-bbbbbb.md", to="qa", status="claimed")
    _write_msg(tmp_path, "MSG-20260603-070002-cccccc.md", to="qa", status="open", mtype="reply")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    assert len(items) == 1  # only the open, non-reply message
    assert items[0]["role"] == "qa"
    assert items[0]["instruction"] == "do the thing"
    assert items[0]["context"]["type"] == "question"


def test_inbox_work_items_carries_routing_metadata(tmp_path):
    _write_msg(
        tmp_path,
        "MSG-20260603-070000-aaaaaa.md",
        to="qa",
        status="open",
        routing_model="auto",
        routing_grade="Low",
        body="find and list files",
    )
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    assert items[0]["routing_model"] == "auto"
    assert items[0]["routing_grade"] == "Low"


def test_inbox_work_items_carries_eval_baseline_and_task_id(tmp_path):
    _write_msg(
        tmp_path,
        "MSG-20260603-070000-aaaaaa.md",
        to="qa",
        status="open",
        routing_model="auto",
        routing_grade="Low",
        body="find and list files",
    )
    msg = tmp_path / "MSG-20260603-070000-aaaaaa.md"
    msg.write_text(
        msg.read_text(encoding="utf-8").replace(
            "routing_grade: Low\n",
            "routing_grade: Low\ntask_id: none\neval_baseline_tokens: 3000\n",
        ),
        encoding="utf-8",
    )
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    assert items[0]["context"]["task_id"] == "MSG-20260603-070000-aaaaaa"
    assert items[0]["eval_baseline_tokens"] == "3000"


def test_inbox_work_items_filters_by_role(tmp_path):
    _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    _write_msg(tmp_path, "MSG-20260603-070001-bbbbbb.md", to="backend", status="open")
    qa = auto_dispatch.inbox_work_items("qa", inbox_dir=tmp_path)
    assert [i["role"] for i in qa] == ["qa"]


def test_inbox_work_items_bounded_by_limit(tmp_path):
    for i in range(5):
        _write_msg(tmp_path, f"MSG-20260603-07000{i}-aaaaa{i}.md", to="qa", status="open")
    items = auto_dispatch.inbox_work_items(limit=2, inbox_dir=tmp_path)
    assert len(items) == 2  # never builds more than `limit`


def test_inbox_work_items_is_read_only(tmp_path):
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    before = msg.read_text(encoding="utf-8")
    auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    assert msg.read_text(encoding="utf-8") == before  # snapshot did not claim/mutate


def test_inbox_work_items_missing_dir_returns_empty(tmp_path):
    assert auto_dispatch.inbox_work_items(inbox_dir=tmp_path / "nope") == []


def test_inbox_items_run_through_dispatch(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    _write_msg(tmp_path, "MSG-20260603-070001-bbbbbb.md", to="backend", status="open")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    summary = run_bounded_dispatch(items, "dummy", max_dispatches=10, out=io.StringIO())
    assert summary["dispatched"] == 2
    assert summary["halt_reason"] == "work_exhausted"


def test_dispatch_records_routing_result(patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=1))
    items = [{
        "role": "qa",
        "instruction": "find and list files",
        "context": {"task_id": "TASK-239"},
        "routing_model": "auto",
        "routing_grade": "Low",
    }]
    summary = _run(items, p, session_budget=1000, max_dispatches=10)
    result = summary["results"][0]
    assert result["routing_grade"] == "Low"
    assert result["policy_model"] == "haiku"
    assert result["selected_model"] == "haiku"


def test_dispatch_records_routed_eval_outcome_when_baseline_present(tmp_path, patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=12, model="haiku"))
    items = [{
        "role": "qa",
        "instruction": "find and list files",
        "context": {"task_id": "TASK-239"},
        "routing_model": "auto",
        "routing_grade": "Low",
        "eval_baseline_tokens": 3000,
    }]
    summary = _run(items, p, session_budget=1000, max_dispatches=10, eval_log_path=tmp_path / "eval.jsonl")
    assert summary["results"][0]["eval_recorded"] is True
    recs = auto_dispatch.eval_harness.read_outcomes(tmp_path / "eval.jsonl")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["task_id"] == "TASK-239"
    assert rec["grade"] == "Low"
    assert rec["tokens"] == 12
    assert rec["policy_model"] == "haiku"
    assert rec["selected_model"] == "haiku"
    assert rec["baseline_tokens"] == 3000


def test_auto_dispatch_records_eval_on_provider_exception_non_write_back(tmp_path, patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=12, raise_on=0, model="haiku"))
    items = [{
        "role": "qa",
        "instruction": "find and list files",
        "context": {"task_id": "TASK-239"},
        "routing_model": "auto",
        "routing_grade": "Low",
        "eval_baseline_tokens": 3000,
    }]
    summary = _run(items, p, session_budget=1000, max_dispatches=10, eval_log_path=tmp_path / "eval.jsonl")
    assert summary["results"][0]["eval_recorded"] is True
    rec = auto_dispatch.eval_harness.read_outcomes(tmp_path / "eval.jsonl")[0]
    assert rec["finish_reason"] == "error"
    assert rec["outcome"] == "gate-error"
    assert rec["actual_tokens_known"] is False
    assert auto_dispatch.eval_harness.judge_outcome(rec) == "escalate"


def test_routing_eval_requires_applied_provider_model(tmp_path, patch_provider):
    p = patch_provider(_FakeProvider(tokens_per_call=12, model="gpt-5.2-codex"))
    items = [{
        "role": "qa",
        "instruction": "find and list files",
        "context": {"task_id": "TASK-239"},
        "routing_model": "auto",
        "routing_grade": "Low",
        "eval_baseline_tokens": 3000,
    }]
    summary = run_bounded_dispatch(
        items,
        "codex-agent",
        session_budget=1000,
        max_dispatches=10,
        eval_log_path=tmp_path / "eval.jsonl",
        out=io.StringIO(),
    )
    result = summary["results"][0]
    assert result["selected_model"] == "haiku"
    assert result["eval_recorded"] is False
    assert result["eval_skip_reason"] == "routing_not_applied"
    assert not (tmp_path / "eval.jsonl").exists()


# ---- write-back path (TASK-212) ----

def _reply_metas(inbox, exclude):
    from agent_worker import parse_frontmatter
    out = []
    for p in inbox.iterdir():
        if p.name == exclude or p.suffix != ".md":
            continue
        out.append(parse_frontmatter(p.read_text(encoding="utf-8"))[0])
    return out


def _status_of(path):
    from agent_worker import parse_frontmatter
    return parse_frontmatter(path.read_text(encoding="utf-8"))[0]["status"]


def test_write_back_replies_and_marks_answered(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    summary = run_bounded_dispatch(items, "dummy", max_dispatches=10,
                                   write_back=True, out=io.StringIO())
    assert summary["dispatched"] == 1
    assert summary["replied"] == 1
    assert summary["spent"] > 0  # dummy reports a non-zero token estimate
    # original walked open -> claimed -> answered (same lifecycle as a worker)
    assert _status_of(msg) == "answered"
    # a reply addressed back to the sender was written into the same inbox
    replies = _reply_metas(tmp_path, exclude=msg.name)
    assert any(m.get("type") == "reply"
               and m.get("in_reply_to") == "MSG-20260603-070000-aaaaaa"
               and m.get("to") == "backend"  # original's `from`
               for m in replies)


def test_write_back_skips_when_not_open_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)  # snapshot while open
    # a worker claims it before dispatch — the snapshot is now stale
    _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="claimed")
    summary = run_bounded_dispatch(items, "dummy", max_dispatches=10,
                                   write_back=True, out=io.StringIO())
    r = summary["results"][0]
    assert r["finish_reason"] == "skipped"
    assert r["error"] == "claim_lost"
    assert summary["spent"] == 0   # claim lost => no billable call
    assert summary["replied"] == 0
    assert _status_of(msg) == "claimed"  # we did not touch the worker's claim
    assert _reply_metas(tmp_path, exclude=msg.name) == []


def test_write_back_provider_error_still_answers(tmp_path, patch_provider):
    p = patch_provider(_FakeProvider(raise_on=0))
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    summary = _run(items, p, max_dispatches=10, write_back=True)
    assert summary["results"][0]["finish_reason"] == "error"
    assert summary["replied"] == 1               # error reply still written...
    assert _status_of(msg) == "answered"         # ...so the claim is not orphaned


def test_write_back_reports_reply_even_if_mark_answered_fails(tmp_path, monkeypatch):
    # If the reply is written but the status flip raises (IO error), the reply
    # must still be reported (accounting correct); message stays 'claimed'.
    import agent_worker
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)

    def _boom(_path):
        raise OSError("disk full")
    monkeypatch.setattr(agent_worker, "mark_answered", _boom)

    summary = run_bounded_dispatch(items, "dummy", max_dispatches=10,
                                   write_back=True, out=io.StringIO())
    assert summary["replied"] == 1                       # reply still written...
    assert summary["results"][0]["reply"] is not None
    assert _status_of(msg) == "claimed"                  # ...flip failed: left claimed
    assert _reply_metas(tmp_path, exclude=msg.name)      # a reply file exists


def test_write_back_off_keeps_inbox_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCH_ENABLE_LIVE", raising=False)
    msg = _write_msg(tmp_path, "MSG-20260603-070000-aaaaaa.md", to="qa", status="open")
    before = msg.read_text(encoding="utf-8")
    items = auto_dispatch.inbox_work_items(inbox_dir=tmp_path)
    summary = run_bounded_dispatch(items, "dummy", max_dispatches=10, out=io.StringIO())
    assert summary["dispatched"] == 1
    assert summary.get("replied", 0) == 0
    assert msg.read_text(encoding="utf-8") == before     # original untouched
    assert [p.name for p in tmp_path.iterdir()] == [msg.name]  # no reply file
