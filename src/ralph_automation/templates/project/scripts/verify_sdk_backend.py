#!/usr/bin/env python3
"""Live verification helper for ClaudeProvider sdk backend (TASK-102 인수사항 / #7).

The sdk backend was only mocked in TASK-102. This runs a real single-shot call so
the success path (content extraction, token usage, finish_reason) is verified once
a valid key is present.

Usage:
  1. Put your Anthropic API key in .env or the environment (gitignored — never commit).
  2. python scripts/verify_sdk_backend.py

Reads .env via python-dotenv, forces CLAUDE_PROVIDER_BACKEND=sdk, calls run() with a
tiny prompt, and prints the ProviderResult. Exit 0 on a real reply, 1 otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass  # dotenv optional; env may already be set

os.environ["CLAUDE_PROVIDER_BACKEND"] = "sdk"

from providers import get_provider  # noqa: E402
from providers.base import ProviderAuthError, ProviderError, ProviderResult  # noqa: E402


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("BLOCKED: ANTHROPIC_API_KEY not set. Add it to .env (gitignored) "
              "or the environment, then re-run.")
        return 1

    provider = get_provider("claude")
    print(f"backend={provider.backend} model={provider.model}")
    prompt_role, instruction = "qa", "Reply with exactly: OK"
    try:
        result: ProviderResult = provider.run(prompt_role, instruction, {"task_id": "verify-sdk"})
    except ProviderAuthError as exc:
        print(f"AUTH FAIL: {exc}")
        return 1
    except ProviderError as exc:
        print(f"PROVIDER ERROR: {exc}")
        return 1

    print("--- live ProviderResult ---")
    print(f"text          : {result.text!r}")
    print(f"tokens_in     : {result.tokens_in}")
    print(f"tokens_out    : {result.tokens_out}")
    print(f"finish_reason : {result.finish_reason}")
    ok = bool(result.text) and result.tokens_out > 0
    print(f"\n{'PASS' if ok else 'INCOMPLETE'}: sdk backend "
          f"{'returned a real reply with usage' if ok else 'reply/usage missing'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
