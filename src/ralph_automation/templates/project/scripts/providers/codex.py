"""Codex providers backed by the OpenAI Responses API (TASK-135).

`CodexProvider` is a single-shot text provider for `agent_worker --provider
codex`. `CodexAgentProvider` mirrors the existing `claude-agent` path: it gives
GPT-5.2-Codex a small, guarded tool surface for repo-local read/edit/test work.

This is deliberately separate from Codex session subagents (`multi_agent_v1`).
Those tools are only callable by the parent Codex session, not by repository
Python. See `scripts/codex_subagent_bridge.py` for the session bridge.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from .agent_tools import TOOLS, ToolRunner
from .base import Provider, ProviderAuthError, ProviderError, ProviderResult, ProviderTimeout
from ._loop_guard import loop_guard_abort_reason

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

DEFAULT_MODEL = "gpt-5.2-codex"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_OUTPUT_TOKENS = 2048
DEFAULT_AGENT_MAX_CALLS = 40
DEFAULT_BASE_URL = "https://api.openai.com/v1"
_AUTH_HINTS = ("auth", "unauthorized", "api key", "api_key", "credential", "401", "403")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOTENV_LOADED = False


def _ensure_env_loaded() -> None:
    """Load repo .env for direct provider invocation paths."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED or load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env")
    _DOTENV_LOADED = True


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _json_text(data: object) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except TypeError:
        return str(data)


class OpenAIResponsesClient:
    """Tiny REST client so the repo does not need a new SDK dependency."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create(self, payload: dict) -> dict:
        url = f"{self.base_url}/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.Timeout as exc:
            raise ProviderTimeout(f"OpenAI Responses API timed out after {self.timeout}s") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"OpenAI Responses API request failed: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            body = (resp.text or "").strip()
            raise ProviderError(
                f"OpenAI Responses API returned non-JSON status {resp.status_code}: {body}"
            ) from exc

        if resp.status_code in {401, 403}:
            raise ProviderAuthError(_format_api_error(data, resp.status_code))
        if resp.status_code >= 400:
            msg = _format_api_error(data, resp.status_code)
            if any(h in msg.lower() for h in _AUTH_HINTS):
                raise ProviderAuthError(msg)
            raise ProviderError(msg)
        if isinstance(data, dict) and data.get("error"):
            msg = _format_api_error(data, resp.status_code)
            if any(h in msg.lower() for h in _AUTH_HINTS):
                raise ProviderAuthError(msg)
            raise ProviderError(msg)
        if not isinstance(data, dict):
            raise ProviderError(f"OpenAI Responses API returned non-object JSON: {data!r}")
        return data


def _format_api_error(data: dict, status_code: int) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or _json_text(err)
    else:
        msg = _json_text(data)
    return f"OpenAI Responses API status {status_code}: {msg}"


def _extract_output_text(data: dict) -> str:
    """Extract text from a Responses API object across REST shapes."""
    direct = data.get("output_text")
    if isinstance(direct, str):
        return direct

    pieces: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "output_text" and isinstance(item.get("text"), str):
            pieces.append(item["text"])
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            ctype = content.get("type")
            if ctype in {"output_text", "text"} and isinstance(content.get("text"), str):
                pieces.append(content["text"])
    return "".join(pieces)


def _usage(data: dict) -> tuple[int, int]:
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    return (
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
    )


def _function_calls(data: dict) -> list[dict]:
    calls: list[dict] = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            calls.append(item)
    return calls


def _openai_tools() -> list[dict]:
    out: list[dict] = []
    for tool in TOOLS:
        schema = dict(tool["input_schema"])
        out.append({
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": schema,
        })
    return out


class CodexProvider(Provider):
    name = "codex"

    def __init__(self) -> None:
        _ensure_env_loaded()
        self.model = os.environ.get("CODEX_PROVIDER_MODEL", DEFAULT_MODEL)
        self.timeout = _env_float("CODEX_PROVIDER_TIMEOUT", DEFAULT_TIMEOUT)
        self.max_output_tokens = _env_int(
            "CODEX_PROVIDER_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS
        )
        self.base_url = os.environ.get("CODEX_PROVIDER_BASE_URL", DEFAULT_BASE_URL)
        self.reasoning_effort = os.environ.get("CODEX_PROVIDER_REASONING_EFFORT", "").strip()

    def _client(self) -> OpenAIResponsesClient:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderAuthError("OPENAI_API_KEY not set for CodexProvider")
        return OpenAIResponsesClient(
            api_key=api_key, base_url=self.base_url, timeout=self.timeout
        )

    def _instructions(self, role: str, task_id: str) -> str:
        return (
            f"You are the '{role}' agent in a multi-agent engineering team "
            f"(task: {task_id}). Respond concisely and cite repository paths "
            "when relevant. Do not claim you changed files unless a tool-enabled "
            "provider actually changed them."
        )

    def _payload(self, role: str, instruction: str, context: dict) -> dict:
        payload: dict = {
            "model": self.model,
            "instructions": self._instructions(role, context.get("task_id", "none")),
            "input": instruction.strip(),
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        return payload

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        data = self._client().create(self._payload(role, instruction, context))
        tokens_in, tokens_out = _usage(data)
        status = str(data.get("status") or "stop")
        return ProviderResult(
            text=_extract_output_text(data),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason=status,
        )


class CodexAgentProvider(CodexProvider):
    """Tool-using OpenAI/Codex backend for repo-local agent work."""

    name = "codex-agent"

    def __init__(self) -> None:
        super().__init__()
        self.max_calls = _env_int("CODEX_AGENT_MAX_CALLS", DEFAULT_AGENT_MAX_CALLS)
        # Per-dispatch cumulative token ceiling (in+out) — runaway/token-waste
        # guardrail (Owner directive, CYCLE-075). Bounds a single run() spend
        # deterministically, independent of max_calls. Env DISPATCH_PER_CALL_CAP.
        self.per_dispatch_cap = _env_int("DISPATCH_PER_CALL_CAP", 80000)

    def _instructions(self, role: str, task_id: str) -> str:
        return (
            f"You are the '{role}' agent in a multi-agent engineering team "
            f"(task: {task_id}). You have tools to read, list, write, edit files "
            "and run whitelisted commands inside the repository. Work ONLY inside "
            "the repository. Do only the requested task, preserve unrelated work, "
            "run relevant verification, then stop with a concise summary. Do NOT "
            "commit or push. If staging is requested, `git add` only explicit "
            "paths you changed; never `git add .`, `-A`, or `--all`."
        )

    def _agent_payload(
        self,
        role: str,
        task_id: str,
        input_items,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "instructions": self._instructions(role, task_id),
            "input": input_items,
            "tools": _openai_tools(),
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        return payload

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        client = self._client()
        root = Path(context.get("working_dir") or ".")
        runner = ToolRunner(root)
        task_id = str(context.get("task_id", "none"))
        input_items = [{"role": "user", "content": instruction.strip()}]
        tokens_in = 0
        tokens_out = 0
        last_text = ""
        started_at = time.monotonic()

        for _ in range(max(self.max_calls, 1)):
            # In-loop guard (CYCLE-079): STOP_LOOP file or wall-clock deadline
            # halts a long-running dispatch before the next billable call.
            guard = loop_guard_abort_reason(started_at, now=time.monotonic())
            if guard is not None:
                return ProviderResult(
                    text=last_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    finish_reason="error",
                    error=f"dispatch halted: {guard}",
                    changed_files=runner.changed_files,
                )
            # Per-dispatch spend ceiling (CYCLE-075): cumulative in+out from
            # prior batches gates the next billable call.
            spent = tokens_in + tokens_out
            if spent >= self.per_dispatch_cap:
                return ProviderResult(
                    text=last_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    finish_reason="error",
                    error=(
                        f"per-dispatch token cap reached: {spent} >= "
                        f"{self.per_dispatch_cap}. Runaway guardrail — raise "
                        "DISPATCH_PER_CALL_CAP if intended."
                    ),
                    changed_files=runner.changed_files,
                )
            data = client.create(self._agent_payload(role, task_id, input_items))
            tin, tout = _usage(data)
            tokens_in += tin
            tokens_out += tout
            last_text = _extract_output_text(data) or last_text
            calls = _function_calls(data)
            if not calls:
                return ProviderResult(
                    text=last_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    finish_reason=str(data.get("status") or "stop"),
                    changed_files=runner.changed_files,
                )

            replay_calls = []
            outputs = []
            for call in calls:
                name = str(call.get("name") or "")
                raw_args = call.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (TypeError, ValueError):
                    args = {}
                result = runner.dispatch(name, args)
                replay_calls.append({
                    "type": "function_call",
                    "call_id": call.get("call_id") or call.get("id"),
                    "name": name,
                    "arguments": raw_args if isinstance(raw_args, str) else json.dumps(args),
                })
                outputs.append({
                    "type": "function_call_output",
                    "call_id": call.get("call_id") or call.get("id"),
                    "output": result,
                })
            # Responses API follow-up turns must include the model's prior
            # function_call item alongside the corresponding function_call_output.
            # Without that explicit pair, non-stored responses can fail with
            # "item not found" lookups against ephemeral response state.
            input_items.extend(replay_calls)
            input_items.extend(outputs)

        return ProviderResult(
            text=last_text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason="error",
            error=f"runaway cap exceeded after {self.max_calls} tool batches",
            changed_files=runner.changed_files,
        )
