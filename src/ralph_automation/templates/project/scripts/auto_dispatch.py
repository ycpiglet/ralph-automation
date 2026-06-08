#!/usr/bin/env python3
"""Bounded synchronous auto-dispatch runner (TASK-208, CYCLE-076).

Stage-7 auto-dispatch that is runaway-proof *by construction* — the Owner's
hard requirement (past incident: an unbounded background loop wasted tokens).

Why this design has no runaway/orphan/race surface:
  - SINGLE process, SYNCHRONOUS: each provider.run() is awaited and its result
    collected before the next dispatch — no fire-and-forget, so no orphaned
    agent can keep billing after the runner moves on.
  - IN-MEMORY cumulative budget: there is no persistent ledger file, so the
    hazards a persistent ledger would carry (cross-process write races, a crash
    window between spend and record, midnight date-key rollover) simply do not
    exist for a single in-process counter (skeptic must-fix, CYCLE-075 → here
    sidestepped rather than mitigated).
  - Layered on the CYCLE-075 guardrails: each provider.run() is itself bounded
    by DISPATCH_PER_CALL_CAP, and get_provider() refuses billable providers
    unless DISPATCH_ENABLE_LIVE=1. So even one dispatch cannot run away, and
    accidental live spend is blocked.

Halt is checked BEFORE every dispatch, on the FIRST of:
  - session token budget exhausted,
  - max_dispatches reached,
  - a stop file present (agents/runtime/STOP_LOOP or .orchestrator-stop),
  - work list exhausted.

The session-budget check is *cumulative and pre-call*: before every provider
call, the runner compares remaining session budget with the provider's
worst-case per-dispatch ceiling. If the next call cannot fit, it is skipped
without spend. Routed agent providers expose `per_dispatch_cap`; providers that
do not expose a ceiling are allowed but cannot weaken the live-agent guardrail.

Default provider is 'dummy' (zero cost). Usage:
  python scripts/auto_dispatch.py --demo                 # dummy, safe
  python scripts/auto_dispatch.py --demo --max-dispatches 3 --session-budget 500
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:  # Windows 콘솔 cp949 에서도 한글 stdout 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from providers import get_provider  # noqa: E402
import model_routing  # noqa: E402
import eval_harness  # noqa: E402

RUNTIME_DIR = REPO_ROOT / "agents" / "runtime"
STOP_LOOP_FILE = RUNTIME_DIR / "STOP_LOOP"
ORCHESTRATOR_STOP_FILE = REPO_ROOT / ".orchestrator-stop"
DEFAULT_STOP_FILES = (STOP_LOOP_FILE, ORCHESTRATOR_STOP_FILE)

# Conservative defaults — anti-runaway first. Override via CLI.
DEFAULT_SESSION_BUDGET = 200_000
DEFAULT_MAX_DISPATCHES = 10


def _routing_decision_for_item(item: dict, instruction: str) -> dict | None:
    context = dict(item.get("context", {}) or {})
    model = item.get("routing_model") or context.get("routing_model") or context.get("model")
    if not model:
        return None
    changed = item.get("routing_changed_files") or context.get("routing_changed_files") or []
    if changed and not isinstance(changed, list):
        changed = [str(changed)]
    try:
        diff_lines = int(item.get("routing_diff_lines") or context.get("routing_diff_lines") or 0)
    except (TypeError, ValueError):
        diff_lines = 0
    return model_routing.resolve_model(
        str(model),
        grade=str(item.get("routing_grade") or context.get("routing_grade") or "Medium"),
        prompt=str(instruction or ""),
        changed_files=changed,
        diff_lines=diff_lines,
    )


def _apply_routing_to_provider(provider, provider_name: str, decision: dict | None) -> None:
    if not decision:
        return
    for name, value in model_routing.provider_env(provider_name, decision["selected_tier"]).items():
        import os
        os.environ[name] = value
        if name == "CLAUDE_AGENT_MODEL" and hasattr(provider, "model"):
            setattr(provider, "model", value)


def _routing_result_fields(decision: dict | None) -> dict:
    if not decision:
        return {}
    return {
        "routing_grade": decision["grade"],
        "policy_model": decision["policy_tier"],
        "selected_model": decision["selected_tier"],
        "routing_signals": list(decision.get("signals") or []),
        "routing_reason": decision.get("reason", ""),
    }


def _positive_int(value) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _eval_baseline_tokens(item: dict) -> int | None:
    context = dict(item.get("context", {}) or {})
    return (
        _positive_int(item.get("eval_baseline_tokens"))
        or _positive_int(item.get("baseline_tokens"))
        or _positive_int(context.get("eval_baseline_tokens"))
        or _positive_int(context.get("baseline_tokens"))
    )


def _metadata_task_id(meta: dict, fallback: str | None = None) -> str:
    task_id = str(meta.get("task_id") or "").strip()
    if task_id and task_id.lower() not in {"none", "unknown", "null"}:
        return task_id
    return str(fallback or meta.get("id") or "none")


def _routing_eval_skip_reason(provider, provider_name: str, routing_decision: dict | None) -> str | None:
    if not routing_decision:
        return None
    selected = str(routing_decision.get("selected_tier") or "").lower()
    if not selected:
        return "routing_not_applied"
    if model_routing.provider_env(provider_name, routing_decision["selected_tier"]):
        return None
    provider_model = str(getattr(provider, "model", None) or "").lower()
    if provider_model == selected:
        return None
    if selected in {"haiku", "sonnet", "opus"} and selected in provider_model:
        return None
    return "routing_not_applied"


def _record_eval_outcome(
    item: dict,
    provider,
    provider_name: str,
    routing_decision: dict | None,
    tokens: int,
    finish_reason: str,
    error,
    eval_log_path: Path | None,
) -> tuple[bool, str | None]:
    if not routing_decision:
        return False, None
    skip_reason = _routing_eval_skip_reason(provider, provider_name, routing_decision)
    if skip_reason:
        return False, skip_reason
    baseline = _eval_baseline_tokens(item)
    if baseline is None:
        return False, None
    context = dict(item.get("context", {}) or {})
    eval_harness.record_outcome(
        str(context.get("task_id") or item.get("task_id") or "none"),
        routing_decision["grade"],
        str(getattr(provider, "model", None) or routing_decision["selected_tier"] or provider_name),
        int(tokens or 0),
        finish_reason=str(finish_reason or "stop"),
        outcome="ok" if not error else "gate-error",
        path=eval_log_path or eval_harness.EVAL_LOG,
        policy_model=routing_decision["policy_tier"],
        selected_model=routing_decision["selected_tier"],
        routing_signals=list(routing_decision.get("signals") or []),
        baseline_tokens=baseline,
        actual_tokens_known=bool(int(tokens or 0) > 0),
    )
    return True, None


@dataclass
class SessionBudget:
    """In-memory cumulative token budget for one runner process.

    No persistence by design (see module docstring). `remaining()` never goes
    negative; `exhausted()` is the halt signal.
    """

    total: int
    spent: int = 0

    def remaining(self) -> int:
        return max(0, self.total - self.spent)

    def exhausted(self) -> bool:
        return self.spent >= self.total

    def record(self, tokens: int) -> None:
        # Spend is monotonic; clamp negatives defensively.
        self.spent += max(0, int(tokens))


def _provider_dispatch_ceiling(provider) -> int | None:
    """Return provider worst-case tokens for one run(), when known."""
    for attr in ("per_dispatch_cap", "tokens_per_call"):
        value = getattr(provider, attr, None)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _budget_skip_result(index: int, role: str, budget: SessionBudget,
                        ceiling: int | None) -> dict:
    result = {
        "index": index,
        "role": role,
        "tokens": 0,
        "finish_reason": "skipped",
        "error": "budget_insufficient",
        "remaining_budget": budget.remaining(),
    }
    if ceiling is not None:
        result["provider_dispatch_ceiling"] = ceiling
    return result


def _stop_file_present(stop_files) -> Path | None:
    for p in stop_files:
        try:
            if Path(p).exists():
                return Path(p)
        except Exception:
            continue
    return None


def _claim_source(path: Path):
    """Re-read a source message fresh and atomically claim open->claimed, using
    the same primitive agent_worker uses. Returns (meta, body) on success, or
    None if it is no longer claimable (already taken / not open / unreadable).

    Re-reading fresh (not trusting the stale snapshot) is what stops an old work
    list from overwriting a message a worker changed since the scan. The residual
    check-then-write window is identical to agent_worker.claim_message's — this
    serializes with a concurrent worker, it does not add a stronger guarantee.
    """
    from agent_worker import claim_message, parse_frontmatter
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not meta or meta.get("status") != "open":
        return None
    if not claim_message(path, meta, body):
        return None
    return meta, body


def _write_back_reply(role: str, src_meta: dict, reply_text: str, source_path: Path):
    """Write the dispatch reply into the source inbox and mark the original
    answered, reusing agent_worker's primitives so the lifecycle is identical to
    a real worker's (open->claimed->answered + a reply message).

    Returns the reply Path, or None if the reply write itself failed. The two
    steps are independent: if the reply is written but the status flip raises
    (an IO error), the reply is still returned (accounting stays correct) and the
    message is left at 'claimed' — the same best-effort the real worker gives
    (agent_worker only logs a WARN on a failed mark_answered). So the orphan-free
    guarantee covers provider errors, not a write-side IO failure on the flip."""
    from agent_worker import mark_answered, write_reply
    try:
        reply_path = write_reply(role, src_meta, reply_text, inbox=source_path.parent)
    except Exception:
        return None
    try:
        mark_answered(source_path)
    except Exception:
        pass  # reply already written; failed flip leaves 'claimed' (worker-equivalent)
    return reply_path


def run_bounded_dispatch(
    work_items: list[dict],
    provider_name: str = "dummy",
    *,
    session_budget: int = DEFAULT_SESSION_BUDGET,
    max_dispatches: int = DEFAULT_MAX_DISPATCHES,
    stop_files=DEFAULT_STOP_FILES,
    write_back: bool = False,
    eval_log_path: Path | None = None,
    out=None,
) -> dict:
    """Dispatch each work item synchronously under hard bounds.

    work_items: list of {"role", "instruction", "context"?} dicts.
    Returns a summary dict: dispatched count, halt_reason, spent, per-item results.
    Never raises on a provider error — it is captured per item so one bad
    dispatch cannot abort accounting (and cannot orphan).

    write_back (TASK-212, default off → read-only as in CYCLE-078): for items that
    carry a "_source_path" (inbox snapshots), claim the source BEFORE the billable
    call, then write the provider's reply back and mark the original answered. The
    claim-before-dispatch order keeps the anti-waste invariant — a lost claim means
    no provider call and so no spend. A provider error still writes an error reply
    so a claimed message is never left orphaned.
    """
    out = out if out is not None else sys.stdout
    provider = get_provider(provider_name)  # live-gate enforced here (CYCLE-075)
    budget = SessionBudget(total=session_budget)
    results: list[dict] = []
    halt_reason = "work_exhausted"

    for i, item in enumerate(work_items):
        # --- halt checks BEFORE any billable call ---
        if i >= max_dispatches:
            halt_reason = f"max_dispatches ({max_dispatches})"
            break
        stop = _stop_file_present(stop_files)
        if stop is not None:
            halt_reason = f"stop_file ({stop.name})"
            break

        role = str(item.get("role", "worker"))
        instruction = str(item.get("instruction", ""))
        context = dict(item.get("context", {}) or {})
        source = item.get("_source_path") if write_back else None
        if budget.exhausted():
            halt_reason = f"session_budget ({budget.total})"
            break
        routing_decision = _routing_decision_for_item(item, instruction)
        _apply_routing_to_provider(provider, provider_name, routing_decision)
        dispatch_ceiling = _provider_dispatch_ceiling(provider)
        if dispatch_ceiling is not None and dispatch_ceiling > budget.remaining():
            halt_reason = f"session_budget ({budget.total})"
            results.append(_budget_skip_result(i, role, budget, dispatch_ceiling))
            break
        if dispatch_ceiling is not None and hasattr(provider, "per_dispatch_cap"):
            try:
                provider.per_dispatch_cap = min(int(provider.per_dispatch_cap), budget.remaining())
            except (TypeError, ValueError):
                pass
        context["session_budget_remaining"] = budget.remaining()
        if routing_decision:
            context["routing"] = routing_decision
            context["provider_model"] = getattr(provider, "model", None)

        if source is not None:
            # --- write-back path: claim BEFORE any billable call ---
            claimed = _claim_source(Path(source))
            if claimed is None:
                # Lost the claim (a worker took it, or it is no longer open).
                # No dispatch => no spend: the anti-waste invariant holds.
                results.append({
                    "index": i, "role": role, "tokens": 0,
                    "finish_reason": "skipped", "error": "claim_lost", "reply": None,
                })
                continue
            src_meta, _ = claimed
            try:
                res = provider.run(role, instruction, context)
                reply_text = (getattr(res, "text", "") or getattr(res, "error", "") or "").strip()
                tokens = int(getattr(res, "tokens_in", 0) or 0) + int(getattr(res, "tokens_out", 0) or 0)
                finish = getattr(res, "finish_reason", "stop")
                err = getattr(res, "error", None)
            except Exception as exc:  # reply with the error so the claim is never orphaned
                reply_text = f"[{role}] dispatch error: {exc.__class__.__name__}: {exc}"
                tokens, finish, err = 0, "error", f"{exc.__class__.__name__}: {exc}"
            budget.record(tokens)  # synchronous: recorded before next dispatch
            eval_recorded, eval_skip_reason = _record_eval_outcome(
                item, provider, provider_name, routing_decision,
                tokens, finish, err, eval_log_path,
            )
            reply_path = _write_back_reply(role, src_meta, reply_text, Path(source))
            result = {
                "index": i, "role": role, "tokens": tokens,
                "finish_reason": finish, "error": err,
                "reply": reply_path.name if reply_path else None,
                "eval_recorded": eval_recorded,
                **_routing_result_fields(routing_decision),
            }
            if eval_skip_reason:
                result["eval_skip_reason"] = eval_skip_reason
            results.append(result)
            continue

        try:
            res = provider.run(role, instruction, context)
            tokens = int(getattr(res, "tokens_in", 0) or 0) + int(getattr(res, "tokens_out", 0) or 0)
            budget.record(tokens)  # synchronous: recorded before next dispatch
            finish = getattr(res, "finish_reason", "stop")
            err = getattr(res, "error", None)
            eval_recorded, eval_skip_reason = _record_eval_outcome(
                item, provider, provider_name, routing_decision,
                tokens, finish, err, eval_log_path,
            )
            result = {
                "index": i, "role": role, "tokens": tokens,
                "finish_reason": finish,
                "error": err,
                "eval_recorded": eval_recorded,
                **_routing_result_fields(routing_decision),
            }
            if eval_skip_reason:
                result["eval_skip_reason"] = eval_skip_reason
            results.append(result)
        except Exception as exc:  # capture — never orphan, never abort accounting
            err = f"{exc.__class__.__name__}: {exc}"
            eval_recorded, eval_skip_reason = _record_eval_outcome(
                item, provider, provider_name, routing_decision,
                0, "error", err, eval_log_path,
            )
            result = {
                "index": i, "role": role, "tokens": 0,
                "finish_reason": "error", "error": err,
                "eval_recorded": eval_recorded,
                **_routing_result_fields(routing_decision),
            }
            if eval_skip_reason:
                result["eval_skip_reason"] = eval_skip_reason
            results.append(result)

    replied = sum(1 for r in results if r.get("reply"))
    summary = {
        "provider": provider_name,
        "dispatched": len(results),
        "halt_reason": halt_reason,
        "spent": budget.spent,
        "session_budget": budget.total,
        "remaining": budget.remaining(),
        "max_dispatches": max_dispatches,
        "write_back": write_back,
        "replied": replied,
        "results": results,
    }
    out.write(
        f"[auto_dispatch] provider={provider_name} dispatched={len(results)} "
        f"halt={halt_reason} spent={budget.spent}/{budget.total} "
        f"remaining={budget.remaining()}"
        + (f" replied={replied}" if write_back else "")
        + "\n"
    )
    return summary


def _demo_items(n: int = 20) -> list[dict]:
    return [
        {"role": "worker", "instruction": f"demo task {k}", "context": {"task_id": "DEMO"}}
        for k in range(n)
    ]


def inbox_work_items(role=None, *, limit=DEFAULT_MAX_DISPATCHES, inbox_dir=None) -> list[dict]:
    """Snapshot pending inbox messages as a bounded work_items list — READ-ONLY.

    This is the work-source adapter that connects the runner to real pending
    work (TASK-210). It deliberately does NOT claim or mutate any message:
    `agent_worker` owns the open->claimed->answered lifecycle, so a read-only
    snapshot cannot race a running worker or orphan a claim. The runner that
    consumes these items also never writes back — the whole inbox path stays
    read-only, which keeps the anti-runaway/orphan invariants intact.

    Returns at most `limit` items (oldest first by filename = timestamp), so the
    dispatch work list is bounded regardless of inbox size. Note the *disk scan*
    is still O(inbox size): we read every file to learn whether it qualifies and
    stop appending at `limit`, but do not short-circuit the directory walk. That
    is fine because the inbox is operationally bounded; we do not silently cap the
    scan (which could drop qualifying messages past an arbitrary cutoff).
    `role=None` snapshots every role; a role string filters to messages to it.
    """
    # Lazy import: reuse agent_worker's frontmatter parser + inbox path rather
    # than re-implementing the schema (its only import side effect is providers,
    # which we already import). Keeps auto_dispatch's core dependency-light.
    from agent_worker import MESSAGES_INBOX, parse_frontmatter

    inbox = Path(inbox_dir) if inbox_dir is not None else MESSAGES_INBOX
    items: list[dict] = []
    if not inbox.is_dir():
        return items
    for p in sorted(inbox.iterdir()):
        if len(items) >= limit:
            break
        if p.suffix != ".md" or p.name.startswith("."):
            continue
        try:
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Same selection as agent_worker.list_inbox_for: open, non-reply only.
        if not meta or meta.get("status") != "open" or meta.get("type") == "reply":
            continue
        to = meta.get("to")
        if role is not None and to != role:
            continue
        msg_id = meta.get("id")
        task_id = _metadata_task_id(meta, msg_id)
        eval_baseline_tokens = meta.get("eval_baseline_tokens") or meta.get("baseline_tokens")
        context = {"msg_id": msg_id, "type": meta.get("type"), "task_id": task_id}
        if eval_baseline_tokens is not None:
            context["eval_baseline_tokens"] = eval_baseline_tokens
        items.append({
            "role": str(to or "worker"),
            "instruction": (body or "").strip() or str(meta.get("subject", "")),
            "context": context,
            "routing_model": meta.get("routing_model"),
            "routing_grade": meta.get("routing_grade"),
            "routing_changed_files": meta.get("routing_changed_files") or [],
            "routing_diff_lines": meta.get("routing_diff_lines") or 0,
            "task_id": task_id,
            "eval_baseline_tokens": eval_baseline_tokens,
            # Source path for the optional write-back path (TASK-212). The
            # snapshot meta/body are deliberately NOT carried: write-back
            # re-reads fresh at claim time so a stale snapshot can never
            # overwrite a message a worker changed since the scan.
            "_source_path": p,
        })
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bounded synchronous auto-dispatch runner")
    ap.add_argument("--provider", default="dummy",
                    help="provider name (dummy=safe default; live needs DISPATCH_ENABLE_LIVE=1)")
    ap.add_argument("--session-budget", type=int, default=DEFAULT_SESSION_BUDGET,
                    help="hard cumulative token budget for this run")
    ap.add_argument("--max-dispatches", type=int, default=DEFAULT_MAX_DISPATCHES,
                    help="hard cap on number of dispatches")
    ap.add_argument("--demo", action="store_true",
                    help="run against generated demo work items (dummy-safe)")
    ap.add_argument("--from-inbox", action="store_true",
                    help="snapshot pending open inbox messages as work (read-only; "
                         "does not claim/mutate — agent_worker owns the lifecycle)")
    ap.add_argument("--role", default=None,
                    help="with --from-inbox, only messages addressed to this role")
    ap.add_argument("--write-back", action="store_true",
                    help="with --from-inbox, claim each message before dispatch and "
                         "write the reply back + mark answered (default off = read-only). "
                         "Claim-before-dispatch means a lost claim costs no tokens.")
    ap.add_argument("--format", choices=["human", "json"], default="human")
    args = ap.parse_args(argv)

    if args.write_back and not args.from_inbox:
        # write-back only acts on items carrying a _source_path (inbox snapshots);
        # with --demo it would silently no-op. Say so rather than mislead.
        print("[auto_dispatch] --write-back has no effect without --from-inbox "
              "(only inbox messages have a source to reply to); ignoring.")
    if args.from_inbox:
        items = inbox_work_items(args.role, limit=args.max_dispatches)
    elif args.demo:
        items = _demo_items()
    else:
        items = []
    if not items:
        print("[auto_dispatch] no work items "
              "(use --demo for a safe dry exercise, or --from-inbox for pending messages)")
        return 0
    # For json output, discard the human progress line into an in-memory sink
    # (no file handle to leak) and print only the structured summary.
    import io
    summary = run_bounded_dispatch(
        items, args.provider,
        session_budget=args.session_budget,
        max_dispatches=args.max_dispatches,
        write_back=args.write_back,
        out=(sys.stdout if args.format == "human" else io.StringIO()),
    )
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
