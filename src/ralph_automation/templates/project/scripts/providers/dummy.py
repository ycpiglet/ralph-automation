"""DummyProvider for agent_worker.py.

Deterministic echo provider with role tagging. Used to prove the worker
runtime loop (poll -> claim -> reply -> status transition + events) without
depending on any external LLM. TASK-101 moved it onto the ProviderResult
interface; the reply text is unchanged so TASK-099 acceptance still holds.
"""

from __future__ import annotations

from .base import Provider, ProviderResult


class DummyProvider(Provider):
    name = "dummy"

    def run(self, role: str, instruction: str, context: dict) -> ProviderResult:
        task_id = context.get("task_id", "none")
        msg_id = context.get("original_msg_id", "unknown")
        text = (
            f"[{role}/dummy] ack {msg_id} (task={task_id}). "
            f"echo: {instruction.strip()}"
        )
        # Best-effort token estimate (word count). Concrete providers report
        # exact counts; DummyProvider only needs a non-zero signal.
        return ProviderResult(
            text=text,
            tokens_in=len(instruction.split()),
            tokens_out=len(text.split()),
            finish_reason="stop",
        )
