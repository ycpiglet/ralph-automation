#!/usr/bin/env python3
"""Adaptive model routing policy (TASK-239).

Deterministic, heuristic-only routing. No online learner, no RouteLLM.
Feedback stays in eval_harness batch reports and human/Lead ratification.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

GRADE_POLICY = {
    "Low": "haiku",
    "Medium": "sonnet",
    "High": "sonnet",
    "Critical": "opus",
}

TIER_ORDER = {"haiku": 1, "sonnet": 2, "opus": 3}

CLAUDE_AGENT_MODEL_ENV = {
    "haiku": ("CLAUDE_AGENT_HAIKU_MODEL", "claude-haiku-4-5"),
    "sonnet": ("CLAUDE_AGENT_SONNET_MODEL", "claude-sonnet-4-5"),
    "opus": ("CLAUDE_AGENT_OPUS_MODEL", "claude-opus-4-7"),
}

SIMPLE_LOOKUP_RE = re.compile(
    r"\b(find|list|read|search|locate|show|grep|rg|status|lookup)\b",
    re.I,
)
DEEP_REASONING_RE = re.compile(
    r"\b(why|investigate|design|architecture|root[- ]?cause|deep|"
    r"threat|security|migration|row-level policy|complex)\b",
    re.I,
)

LARGE_FILE_COUNT = 8
LARGE_DIFF_LINES = 600


def normalize_grade(grade: str | None) -> str:
    if grade in GRADE_POLICY:
        return str(grade)
    return "Medium"


def normalize_tier(tier: str | None) -> str:
    value = str(tier or "").strip().lower()
    if value not in TIER_ORDER:
        raise ValueError(f"unknown model tier '{tier}'. expected one of {sorted(TIER_ORDER)}")
    return value


def infer_tier(model_or_tier: str | None) -> str | None:
    """Infer haiku/sonnet/opus from a tier or provider model name."""
    value = str(model_or_tier or "").strip().lower()
    if value in TIER_ORDER:
        return value
    for tier in TIER_ORDER:
        if tier in value:
            return tier
    return None


def _signals(
    prompt: str = "",
    changed_files: Iterable[str] | None = None,
    diff_lines: int = 0,
) -> list[str]:
    signals: list[str] = []
    text = prompt or ""
    if SIMPLE_LOOKUP_RE.search(text):
        signals.append("simple_lookup")
    if DEEP_REASONING_RE.search(text):
        signals.append("deep_reasoning")
    files = list(changed_files or [])
    if len(files) >= LARGE_FILE_COUNT:
        signals.append("large_file_count")
    if int(diff_lines or 0) >= LARGE_DIFF_LINES:
        signals.append("large_diff")
    return signals


def select_model(
    grade: str | None,
    *,
    prompt: str = "",
    changed_files: Iterable[str] | None = None,
    diff_lines: int = 0,
) -> dict:
    """Return a routing decision dict for a task grade and prompt/surface signals."""
    normalized_grade = normalize_grade(grade)
    policy_tier = GRADE_POLICY[normalized_grade]
    signals = _signals(prompt, changed_files, diff_lines)

    selected_tier = policy_tier
    if any(s in signals for s in ("deep_reasoning", "large_file_count", "large_diff")):
        selected_tier = "opus"
    elif "simple_lookup" in signals and normalized_grade != "Critical":
        selected_tier = "haiku"

    return {
        "grade": normalized_grade,
        "policy_tier": policy_tier,
        "selected_tier": selected_tier,
        "signals": signals,
        "reason": _reason(policy_tier, selected_tier, signals),
    }


def resolve_model(
    model: str | None,
    *,
    grade: str | None = None,
    prompt: str = "",
    changed_files: Iterable[str] | None = None,
    diff_lines: int = 0,
) -> dict:
    """Resolve `auto` or an explicit tier into the common decision shape."""
    value = str(model or "auto").strip().lower()
    if value in {"", "auto"}:
        return select_model(
            grade,
            prompt=prompt,
            changed_files=changed_files,
            diff_lines=diff_lines,
        )
    normalized_grade = normalize_grade(grade)
    policy_tier = GRADE_POLICY[normalized_grade]
    inferred_tier = infer_tier(value)
    if inferred_tier is None:
        selected_tier = value
        signals = ["manual_override", "raw_provider_model"]
        if normalized_grade == "Critical":
            selected_tier = policy_tier
            signals.append("critical_floor")
        return {
            "grade": normalized_grade,
            "policy_tier": policy_tier,
            "selected_tier": selected_tier,
            "signals": signals,
            "reason": (
                f"manual override to {value}; Critical floor kept {policy_tier}"
                if selected_tier != value
                else f"manual override to provider model {value}"
            ),
        }
    tier = inferred_tier
    signals = ["manual_override"]
    selected_tier = tier if value == tier else value
    if normalized_grade == "Critical" and TIER_ORDER[tier] < TIER_ORDER[policy_tier]:
        selected_tier = policy_tier
        signals.append("critical_floor")
    return {
        "grade": normalized_grade,
        "policy_tier": policy_tier,
        "selected_tier": selected_tier,
        "signals": signals,
        "reason": (
            f"manual override to {tier}; Critical floor kept {policy_tier}"
            if selected_tier != tier
            else f"manual override to {tier}"
        ),
    }


def provider_env(provider_name: str, tier_or_model: str) -> dict[str, str]:
    """Return environment variables needed for a provider to use a routed tier."""
    if provider_name != "claude-agent":
        return {}
    value = str(tier_or_model or "").strip()
    lower = value.lower()
    if lower in CLAUDE_AGENT_MODEL_ENV:
        env_name, default_model = CLAUDE_AGENT_MODEL_ENV[lower]
        return {"CLAUDE_AGENT_MODEL": os.environ.get(env_name, default_model)}
    if value:
        return {"CLAUDE_AGENT_MODEL": value}
    return {}


def _reason(policy_tier: str, selected_tier: str, signals: list[str]) -> str:
    if selected_tier == policy_tier and not signals:
        return "grade policy"
    if selected_tier == policy_tier:
        return "grade policy retained despite signals"
    if selected_tier == "opus":
        return "escalated by prompt/surface signal"
    if selected_tier == "haiku":
        return "downrouted by simple lookup signal"
    return "routed by policy"
