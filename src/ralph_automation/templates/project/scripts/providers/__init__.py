"""Provider adapter package for agent_worker.py.

A Provider is the backend that generates an agent's reply. TASK-101 broadened
the interface to `run(...) -> ProviderResult`, a `run_stream` hook, and typed
errors (see base.py). DummyProvider runs on the new interface; Claude/Codex/
OpenAI are registered stubs whose concrete implementation lands separately.
TASK-135 implements CodexProvider / CodexAgentProvider through the OpenAI
Responses API.
"""

import os

from .base import (
    Chunk,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
)
from .claude import ClaudeProvider
from .claude_agent import ClaudeAgentProvider
from .codex import CodexAgentProvider, CodexProvider
from .dummy import DummyProvider
from .openai import OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {
    "dummy": DummyProvider,
    "claude": ClaudeProvider,
    "claude-agent": ClaudeAgentProvider,
    "codex": CodexProvider,
    "codex-agent": CodexAgentProvider,
    "openai": OpenAIProvider,
}


# Billable providers that make external (cost-bearing) LLM calls. These are
# gated behind an explicit env opt-in so no entry point (agent_worker,
# agent_loop, agent_pipeline_panes) can spend tokens by accident — the
# runaway/token-waste guardrail (Owner directive, CYCLE-075). 'dummy' (and the
# 'openai' stub) are always allowed.
LIVE_PROVIDERS = {"claude", "claude-agent", "codex", "codex-agent"}


def get_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise SystemExit(f"unknown provider '{name}'. known: {known}")
    if name in LIVE_PROVIDERS and os.environ.get("DISPATCH_ENABLE_LIVE") != "1":
        raise SystemExit(
            f"live provider '{name}' is billable and gated by a guardrail. "
            "Set DISPATCH_ENABLE_LIVE=1 to enable real token spend, or use "
            "'--provider dummy'. This prevents accidental runaway cost — "
            "agent_worker/agent_loop/pipeline default to the safe path."
        )
    return PROVIDERS[name]()


__all__ = [
    "Provider",
    "ProviderResult",
    "Chunk",
    "ProviderError",
    "ProviderTimeout",
    "ProviderAuthError",
    "DummyProvider",
    "ClaudeProvider",
    "ClaudeAgentProvider",
    "CodexProvider",
    "CodexAgentProvider",
    "OpenAIProvider",
    "PROVIDERS",
    "get_provider",
]
