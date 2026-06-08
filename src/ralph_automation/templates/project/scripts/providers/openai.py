"""OpenAIProvider stub (TASK-101).

Registered so `--provider openai` resolves; concrete implementation is TASK-102+.
Module import must succeed; only `run` raises.
"""

from __future__ import annotations

from .base import Provider, ProviderResult


class OpenAIProvider(Provider):
    name = "openai"

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        raise NotImplementedError("OpenAIProvider lands in TASK-102")
