"""Provider abstract base for agent_worker.py.

TASK-099 shipped a minimal `run(role, instruction, context) -> str` surface.
TASK-101 broadens it (per the original docstring intent) to the full vision:

  - structured result (`ProviderResult`: text + token usage + finish_reason + error)
  - streaming hook (`run_stream` -> Iterator[Chunk]) with a default implementation
    that wraps `run`, so subclasses opt into real streaming but the worker can stay
    on `run` until it needs the stream (TASK-101 keeps the worker on `run`).
  - typed error semantics (`ProviderError` / `ProviderTimeout` / `ProviderAuthError`)

Concrete LLM providers (Claude/Codex/OpenAI) arrive in TASK-102; TASK-101 ships
DummyProvider on the new interface plus empty stubs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


class ProviderError(Exception):
    """Base class for provider failures (TASK-101)."""


class ProviderTimeout(ProviderError):
    """Provider exceeded its time budget."""


class ProviderAuthError(ProviderError):
    """Provider authentication / credential failure."""


@dataclass
class ProviderResult:
    """Structured reply from a provider.

    `text` is the reply body the worker writes. Token counts are best-effort
    (0 when unknown). `error` is set (with `text` typically empty) when a
    provider wants to report a soft failure without raising.
    """

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = "stop"
    error: str | None = None
    changed_files: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    """One streaming delta. `done=True` marks the final chunk."""

    delta: str
    done: bool = False


class Provider:
    """Abstract provider. Subclasses override `run` (and optionally `run_stream`)."""

    name: str = "abstract"

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        raise NotImplementedError

    def run_stream(self, role: str, instruction: str, context: dict) -> Iterator[Chunk]:
        """Default streaming: run once and emit the whole reply as a single
        final chunk. Subclasses with real token streaming override this.
        """
        result = self.run(role, instruction, context)
        yield Chunk(delta=result.text, done=True)
