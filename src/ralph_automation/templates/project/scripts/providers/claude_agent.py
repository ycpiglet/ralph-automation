"""ClaudeAgentProvider — tool-using agent backend (TASK-110, Stage 5 track A).

Unlike ClaudeProvider (single-shot text), this provider runs a tool-use loop on
the anthropic Messages SDK: the model reads/edits files and runs whitelisted
commands inside the repo via ToolRunner until it stops asking for tools. It does
NOT use the `claude` CLI, so it avoids the TASK-104 recursion nondeterminism.

Scope (TASK-110): the agent edits files and runs tests on a feature branch. It
must NOT commit or push (run_command blocks `git push`); commit/PR is gated.

Context budget (TASK-145, ENTRY-007 follow-up): the tool-use loop accumulates
`messages` on every iteration (assistant blocks + tool_result content). Without
a guard, large file reads (e.g. 4000+ line index.html) blow past the Claude API
~200K input-token limit, producing finish_reason=error with partial billing.
A pre-iteration estimator aborts cleanly before the API rejects the call.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .agent_tools import TOOLS, ToolRunner
from .base import Provider, ProviderAuthError, ProviderError, ProviderResult
from ._loop_guard import loop_guard_abort_reason

DEFAULT_MODEL = "claude-opus-4-7"


def _estimate_input_tokens(system: str, messages: list[dict]) -> int:
    """Rough char/4 heuristic to estimate next-call input tokens.

    The Claude API counts tokens precisely; this estimate is conservative and
    only used to abort *before* a 200K+ call. Counts JSON-serialized content for
    list/dict message bodies (tool_use / tool_result blocks) since that mirrors
    what the SDK sends on the wire.
    """
    total_chars = len(system or "")
    for m in messages:
        content = m.get("content")
        if isinstance(content, (list, dict)):
            total_chars += len(json.dumps(content, default=str, ensure_ascii=False))
        else:
            total_chars += len(str(content or ""))
    return total_chars // 4


class ClaudeAgentProvider(Provider):
    name = "claude-agent"

    def __init__(self) -> None:
        self.model = os.environ.get("CLAUDE_AGENT_MODEL", DEFAULT_MODEL)
        self.max_calls = int(os.environ.get("CLAUDE_AGENT_MAX_CALLS", "40"))
        self.max_tokens = int(os.environ.get("CLAUDE_AGENT_MAX_TOKENS", "4096"))
        # Input token budget (TASK-145, ENTRY-007). Default 150K leaves headroom
        # below the ~200K Claude API limit. Override via env for experiments.
        self.input_token_budget = int(
            os.environ.get("CLAUDE_AGENT_INPUT_BUDGET", "150000")
        )
        # Per-dispatch cumulative token ceiling (in+out), checked in-process
        # before each API call. Bounds the WORST-CASE spend of a single run()
        # deterministically, independent of max_calls — the runaway/token-waste
        # guardrail (Owner directive, CYCLE-075). Env DISPATCH_PER_CALL_CAP.
        self.per_dispatch_cap = int(
            os.environ.get("DISPATCH_PER_CALL_CAP", "80000")
        )

    def _system(self, role: str, task_id: str) -> str:
        return (
            f"You are the '{role}' agent in a multi-agent engineering team "
            f"(task: {task_id}). You have tools to read, list, write, and edit "
            "files and to run whitelisted commands (pytest, python, git "
            "status/diff/add/commit). Work ONLY inside the repository. Make the "
            "requested change, run the relevant tests to confirm it works, then "
            "stop and reply with a short summary of what you changed and the test "
            "result. Do NOT commit or push — that is handled separately.\n\n"
            "Fidelity rules: do ONLY the requested task as stated. Do not modify "
            "or delete unrelated work in the repository, and do not substitute "
            "your own judgement of what the task 'should' be. When a commit is "
            "asked for, `git add` only the explicit paths you changed — never "
            "`git add .`, `-A`, or `--all`."
        )

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderAuthError("ANTHROPIC_API_KEY not set for claude-agent")
        try:
            import anthropic  # lazy: only the agent backend needs it
        except ImportError as exc:
            raise ProviderError(
                "anthropic package not installed (pip install anthropic)"
            ) from exc

        root = Path(context.get("working_dir") or ".")
        runner = ToolRunner(root)
        client = anthropic.Anthropic(api_key=api_key)
        system = self._system(role, context.get("task_id", "none"))
        messages: list[dict] = [{"role": "user", "content": instruction.strip()}]

        tokens_in = tokens_out = calls = 0
        started_at = time.monotonic()
        try:
            while True:
                # In-loop guard (CYCLE-079): STOP_LOOP file or wall-clock deadline
                # halts a long-running dispatch before the next billable call.
                guard = loop_guard_abort_reason(started_at, now=time.monotonic())
                if guard is not None:
                    return ProviderResult(
                        text="",
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        finish_reason="error",
                        error=f"dispatch halted: {guard} (after {calls} tool batches)",
                        changed_files=runner.changed_files,
                    )
                # Input budget pre-check (TASK-145, ENTRY-007). Abort before the
                # API rejects on context overflow — partial billing is avoided.
                estimated = _estimate_input_tokens(system, messages)
                if estimated > self.input_token_budget:
                    return ProviderResult(
                        text="",
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        finish_reason="error",
                        error=(
                            f"input budget exceeded: ~{estimated} tokens > "
                            f"{self.input_token_budget} cap "
                            f"(after {calls} tool batches). Raise "
                            "CLAUDE_AGENT_INPUT_BUDGET or shrink the task."
                        ),
                        changed_files=runner.changed_files,
                    )
                # Per-dispatch spend ceiling (CYCLE-075). Cumulative in+out from
                # prior batches gates the NEXT (billable) call — bounds total
                # spend of this run() even if max_calls is large.
                spent = tokens_in + tokens_out
                if spent >= self.per_dispatch_cap:
                    return ProviderResult(
                        text="",
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        finish_reason="error",
                        error=(
                            f"per-dispatch token cap reached: {spent} >= "
                            f"{self.per_dispatch_cap} (after {calls} tool batches). "
                            "Runaway guardrail — raise DISPATCH_PER_CALL_CAP if intended."
                        ),
                        changed_files=runner.changed_files,
                    )
                # Note: no `temperature` — claude-opus-4-7 deprecates it (API 400).
                # The model is near-deterministic at default settings; forcing
                # temperature is neither needed nor accepted (TASK-110 live canary).
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                )
                usage = getattr(resp, "usage", None)
                if usage:
                    tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
                    tokens_out += int(getattr(usage, "output_tokens", 0) or 0)

                blocks = list(resp.content)
                text = "".join(
                    b.text for b in blocks
                    if getattr(b, "type", None) == "text" and getattr(b, "text", None)
                )

                if resp.stop_reason != "tool_use":
                    return ProviderResult(
                        text=text, tokens_in=tokens_in, tokens_out=tokens_out,
                        finish_reason=resp.stop_reason or "stop",
                        changed_files=runner.changed_files,
                    )

                messages.append({"role": "assistant", "content": blocks})
                results = []
                for b in blocks:
                    if getattr(b, "type", None) == "tool_use":
                        out = runner.dispatch(b.name, dict(b.input or {}))
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": out,
                        })
                messages.append({"role": "user", "content": results})

                calls += 1
                if calls >= self.max_calls:
                    return ProviderResult(
                        text=text or "",
                        tokens_in=tokens_in, tokens_out=tokens_out,
                        finish_reason="error",
                        error=f"runaway cap exceeded after {calls} tool batches",
                        changed_files=runner.changed_files,
                    )
        except (ProviderError, ProviderAuthError):
            raise
        except Exception as exc:  # surface anthropic/network errors as ProviderError
            raise ProviderError(f"claude-agent call failed: {exc}") from exc
