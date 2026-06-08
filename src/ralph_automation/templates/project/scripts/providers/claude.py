"""ClaudeProvider — non-interactive Claude backend (TASK-102).

First concrete implementation of the TASK-101 Provider interface. Two backends,
selected by the `CLAUDE_PROVIDER_BACKEND` env var (default "cli"):

  - cli  : single-shot `claude -p "<prompt>" --output-format json` subprocess.
           No API key / no new dependency — reuses the installed Claude Code CLI.
  - sdk  : Anthropic Python SDK (lazy-imported). Needs `anthropic` + ANTHROPIC_API_KEY.

Both converge on ProviderResult. Always a fresh single-shot call — never controls
an already-running Claude pane (AGENT_RUNTIME.md §9). run_stream stays on the base
default (wrap run) — real token streaming is a follow-up.

TASK-104 (CLI backend 한계): `claude` 는 stateless 완성 API 가 아니라 풀 Claude Code
에이전트다. Claude Code 세션 안에서 `claude -p` 를 subprocess 로 재귀 호출하면
(cwd=repo, CLAUDECODE 상속) 비자명한 프롬프트에 대해 비결정적으로 동작한다 — 정상
완성 / 프로젝트 인식 에이전트화(usage 0·장문) / 빈 출력 사이를 오가며, env scrub 으로도
안정화되지 않는다(evidence/TASK-104/investigation.md). 따라서 CLI backend 는 best-effort
이며, 결정적 production 경로는 SDK backend(ANTHROPIC_API_KEY, #7 live 검증 완료)다.
신뢰성이 필요하면 CLAUDE_PROVIDER_BACKEND=sdk 를 쓴다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from collections.abc import Iterator
from pathlib import Path

from .base import (
    Chunk,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
)

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - optional dependency
    dotenv_values = None

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 1024
_AUTH_HINTS = ("auth", "login", "unauthorized", "api key", "api-key", "credential", "401")
# Hints that the *account behind a key* can't serve right now (credit/billing) —
# distinct from a transient server error. On any of these (or an auth hint) we
# fall through to the next configured key so two accounts can be dropped in and
# the funded one used. Kept tight (specific phrases) so a generic 500/timeout does
# NOT burn a billable retry on the next key.
_KEY_FALLBACK_HINTS = _AUTH_HINTS + ("credit balance", "insufficient_quota", "billing")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOTENV_LOADED = False


def _ensure_anthropic_keys_loaded() -> None:
    """Populate ANTHROPIC_API_KEY* from repo .env if absent — KEYS ONLY.

    Deliberately NOT a blanket load_dotenv: it only fills the ANTHROPIC_API_KEY /
    ANTHROPIC_API_KEYS / ANTHROPIC_API_KEY_N names, leaving backend/model/timeout
    selection on process-env-or-code-default (a full .env load would let .env flip
    CLAUDE_PROVIDER_BACKEND for every caller — out of scope). An already-set env
    var is never overwritten. One-shot per process: editing .env mid-run needs a
    fresh process to be picked up."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED or dotenv_values is None:
        return
    _DOTENV_LOADED = True
    try:
        values = dotenv_values(REPO_ROOT / ".env")
    except Exception:
        return
    for name, val in (values or {}).items():
        is_key = name in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS") or (
            name.startswith("ANTHROPIC_API_KEY_") and name[len("ANTHROPIC_API_KEY_"):].isdigit()
        )
        if is_key and val and not os.environ.get(name):
            os.environ[name] = val


def _anthropic_keys() -> list[str]:
    """API keys to try, in priority order, supporting multiple accounts:
    ANTHROPIC_API_KEY (primary), a comma-separated ANTHROPIC_API_KEYS, and any
    ANTHROPIC_API_KEY_<suffix> — numbered (_2) OR named (_KETI, _PERSONAL).
    Deduped, empties dropped. Ordering after primary/list: numeric suffixes
    ascending, then named suffixes alphabetically (deterministic). Lets an
    operator drop in keys for several accounts under whatever names they like;
    _run_sdk falls through to whichever has credit."""
    raw: list[str] = []
    primary = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if primary:
        raw.append(primary)
    for k in os.environ.get("ANTHROPIC_API_KEYS", "").split(","):
        if k.strip():
            raw.append(k.strip())
    # Any ANTHROPIC_API_KEY_<suffix> env var. startswith("ANTHROPIC_API_KEY_")
    # excludes ANTHROPIC_API_KEYS (no trailing underscore) and the bare primary.
    prefix = "ANTHROPIC_API_KEY_"
    suffixed: list[tuple[tuple[int, object], str]] = []
    for name, val in os.environ.items():
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        v = (val or "").strip()
        if not suffix or not v:
            continue
        # numeric suffixes sort before named ones, each group ordered naturally
        sort_key = (0, int(suffix)) if suffix.isdigit() else (1, suffix)
        suffixed.append((sort_key, v))
    for _, v in sorted(suffixed, key=lambda t: t[0]):
        raw.append(v)
    seen: set[str] = set()
    keys: list[str] = []
    for k in raw:
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self) -> None:
        # get_provider() instantiates with no args, so backend selection lives
        # in the constructor via env (TASK-102 인수 사항).
        _ensure_anthropic_keys_loaded()  # fill ANTHROPIC_API_KEY* from .env (keys only)
        self.backend = os.environ.get("CLAUDE_PROVIDER_BACKEND", "cli").strip().lower()
        self.model = os.environ.get("CLAUDE_PROVIDER_MODEL", DEFAULT_MODEL)
        try:
            self.timeout = float(os.environ.get("CLAUDE_PROVIDER_TIMEOUT", DEFAULT_TIMEOUT))
        except ValueError:
            self.timeout = DEFAULT_TIMEOUT
        # TASK-104: cli backend invoked from *inside* a Claude Code session
        # (CLAUDECODE set) recurses into the full agent and is nondeterministic.
        # Warn once and point to the deterministic sdk backend.
        if self.backend == "cli" and os.environ.get("CLAUDECODE"):
            print(
                "[ClaudeProvider] warning: cli backend is unreliable when run inside "
                "Claude Code (nested agent, nondeterministic output — TASK-104). "
                "Set CLAUDE_PROVIDER_BACKEND=sdk for deterministic results.",
                file=sys.stderr, flush=True,
            )

    # ---- prompt ----

    def _build_prompt(self, role: str, instruction: str, context: dict) -> str:
        task_id = context.get("task_id", "none")
        return (
            f"You are the '{role}' agent in a multi-agent engineering team "
            f"(task: {task_id}). Respond concisely to the request below.\n\n"
            f"{instruction.strip()}"
        )

    # ---- dispatch ----

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        prompt = self._build_prompt(role, instruction, context)
        if self.backend == "sdk":
            return self._run_sdk(prompt)
        return self._run_cli(prompt)

    def run_stream(self, role: str, instruction: str, context: dict) -> Iterator[Chunk]:
        # CLI backend streams real token chunks via stream-json. SDK backend
        # keeps the base default (wrap run) — SDK streaming is a follow-up.
        if self.backend == "sdk":
            yield from super().run_stream(role, instruction, context)
            return
        yield from self._run_cli_stream(self._build_prompt(role, instruction, context))

    # ---- cli backend ----

    def _run_cli(self, prompt: str) -> ProviderResult:
        # Resolve the full executable path. On Windows `claude` is a `.cmd`
        # wrapper that CreateProcess can't find from the bare name (no PATHEXT
        # resolution); shutil.which honours PATHEXT and returns claude.CMD.
        exe = shutil.which("claude")
        if not exe:
            raise ProviderError("claude CLI not found on PATH")
        cmd = [exe, "-p", prompt, "--output-format", "json"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout,
                # claude -p reads stdin and appends it to the prompt; without this
                # it waits ~3s then runs a degraded path (empty usage, odd reply).
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ProviderError(f"claude CLI not found on PATH: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeout(f"claude CLI timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if any(h in stderr.lower() for h in _AUTH_HINTS):
                raise ProviderAuthError(f"claude CLI auth failure: {stderr}")
            raise ProviderError(f"claude CLI exited {proc.returncode}: {stderr}")
        return self._parse_cli_json(proc.stdout)

    def _parse_cli_json(self, stdout: str) -> ProviderResult:
        raw = (stdout or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Defensive: a CLI build that prints plain text → use it as the reply.
            return ProviderResult(text=raw, finish_reason="stop")
        if not isinstance(data, dict):
            return ProviderResult(text=str(data), finish_reason="stop")
        # Defensive key lookup across CLI versions.
        text = data.get("result") or data.get("text") or data.get("content") or ""
        if isinstance(text, list):  # content-block array
            text = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in text
            )
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("input_tokens", 0) or 0)
        tokens_out = int(usage.get("output_tokens", 0) or 0)
        if data.get("is_error"):
            return ProviderResult(
                text=str(text), tokens_in=tokens_in, tokens_out=tokens_out,
                finish_reason="error", error=str(text) or "claude reported is_error",
            )
        # Real `claude --output-format json` carries top-level stop_reason
        # (e.g. "end_turn") — keep it for parity with the sdk backend.
        finish = data.get("stop_reason") or "stop"
        return ProviderResult(
            text=str(text), tokens_in=tokens_in, tokens_out=tokens_out, finish_reason=finish,
        )

    def _run_cli_stream(self, prompt: str) -> Iterator[Chunk]:
        # stream-json emits one JSON event per line (requires --verbose with -p).
        # assistant events carry text content blocks; the final result event
        # closes the stream. We yield a Chunk per assistant text block.
        exe = shutil.which("claude")
        if not exe:
            raise ProviderError("claude CLI not found on PATH")
        cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,  # see _run_cli — avoid the stdin-wait degraded path
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError as exc:
            raise ProviderError(f"claude CLI not found on PATH: {exc}") from exc

        emitted = False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        emitted = True
                        yield Chunk(delta=block["text"], done=False)
            elif etype == "result":
                # Fallback: if no assistant text streamed, emit the final result text.
                text = ev.get("result") or ""
                if text and not emitted:
                    emitted = True
                    yield Chunk(delta=str(text), done=False)

        ret = proc.wait()
        if ret != 0:
            stderr = (proc.stderr.read() if proc.stderr else "") or ""
            stderr = stderr.strip()
            if any(h in stderr.lower() for h in _AUTH_HINTS):
                raise ProviderAuthError(f"claude CLI auth failure: {stderr}")
            raise ProviderError(f"claude CLI (stream) exited {ret}: {stderr}")
        yield Chunk(delta="", done=True)

    # ---- sdk backend (lazy) ----

    def _run_sdk(self, prompt: str) -> ProviderResult:
        keys = _anthropic_keys()
        if not keys:
            raise ProviderAuthError("ANTHROPIC_API_KEY not set for sdk backend")
        try:
            import anthropic  # lazy: cli users never need this installed
        except ImportError as exc:
            raise ProviderError(
                "anthropic package not installed (pip install anthropic) for sdk backend"
            ) from exc
        max_tokens = int(os.environ.get("CLAUDE_PROVIDER_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        last_exc: Exception | None = None
        for idx, api_key in enumerate(keys):
            try:
                client = anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                last_exc = exc
                # If another configured key might still serve (credit/quota/auth),
                # try it before giving up — this is the two-accounts fallback.
                if idx < len(keys) - 1 and any(h in str(exc).lower() for h in _KEY_FALLBACK_HINTS):
                    continue
                break
            return self._parse_sdk_resp(resp)
        # stopped: keys exhausted, or a non-fallback error (no point rotating)
        suffix = f" (all {len(keys)} keys failed)" if len(keys) > 1 else ""
        if any(h in str(last_exc).lower() for h in _AUTH_HINTS):
            raise ProviderAuthError(f"anthropic auth failure{suffix}: {last_exc}") from last_exc
        raise ProviderError(f"anthropic call failed{suffix}: {last_exc}") from last_exc

    @staticmethod
    def _parse_sdk_resp(resp) -> ProviderResult:
        text = "".join(
            getattr(block, "text", "") for block in getattr(resp, "content", [])
            if getattr(block, "type", None) == "text"
        )
        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        finish = getattr(resp, "stop_reason", None) or "stop"
        return ProviderResult(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, finish_reason=finish,
        )
