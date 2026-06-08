# Codex Bootstrap Prompt

```text
You are working in this repository as a coding agent.

Before changing files, read:
1. AGENTS.md
2. README.md
3. agents/lead_engineer/STATUS.md
4. agents/lead_engineer/AUDIT-LOG.md
5. agents/lead_engineer/compound_log.md
6. the latest agents/lead_engineer/CYCLE-*.md by highest number
7. related agents/lead_engineer/reviews/REVIEW-*.md
8. latest agents/{role}/retros/RETRO-{role}-*.md (your role's last RETRO, if any)
9. agents/lead_engineer/tasks/INDEX.md
10. related agents/lead_engineer/tasks/TASK-*.md

Build a private Context Snapshot with:
- latest cycle
- current request
- related TASK/BTC/BUG
- owner
- scope and out-of-scope items
- completion criteria
- verification method

Codex-specific subagent boundary:
- If the current Codex environment exposes `multi_agent_v1` tools and the user
  explicitly asks for subagents, delegation, or parallel agent work, you may
  call Codex subagents directly from the parent Codex session.
- Treat Codex subagents as session-layer tools, not repository runtime workers.
  They are not `scripts/providers/codex.py` and not
  `agent_worker --provider codex`.
- Prefer `python scripts/codex_subagent_bridge.py dispatch --emit-call` before
  session-layer Codex delegation when the result should be auditable. Use the
  rendered packet prompt with `multi_agent_v1.spawn_agent`, then close the loop
  with `record-reply` or `council-record`.
- Repository runtime Codex workers are separate: `agent_worker --provider codex`
  is a single-shot OpenAI Responses API provider, and `--provider codex-agent`
  is a guarded ToolRunner provider for repo-local read/edit/test work. Both
  require `OPENAI_API_KEY`.
- Scheduled local fallback: if Windows Task Scheduler reports
  `LastTaskResult=255` or is not visible from the Codex sandbox, do not treat
  OS scheduling as healthy. Use `python scripts/local_schedule_daemon.py status`
  to inspect the user-session fallback, `tick --force` for a one-shot R1 notify
  smoke, and `watch --interval 60 --run-now` for an always-on local loop while
  the machine/session is awake.
- Token-efficient scheduling: the local loop must stay deterministic. It does
  not call an LLM while idle; current notify schedules run only
  `maintenance`/`digest` read-only scripts. Use Codex Automations for low
  frequency LLM work such as morning briefs or weekly reviews; those runs may
  count against Codex/agentic usage.
- When Codex subagents are used for repo work, record the evidence in the
  active TASK (`subagents_used`), review/audit notes, or collaboration log as
  appropriate.
- If those tools are unavailable, say so plainly and use the repository's
  message-bus / dummy / Claude-provider / Codex API-provider paths instead.

Follow the repository editing rules:
- preserve user changes and check git status before edits
- use rg for search
- use apply_patch for manual edits
- keep changes scoped to the active TASK
- do not create duplicate TASK/BTC/BUG records

When documentation or agent coordination rules change, run:
python scripts/check_agent_docs.py

Codex final reporting rule:
- If you performed substantive work in the session, including tool execution,
  file edits, scheduler/runtime checks, tests, or rule hardening, treat the
  final chat response as an Owner/CEO-facing report even if the user did not
  say "report".
- Follow the same rule as CLAUDE.md §5.3: the first line must start with
  `Bottom Line:` and the response should use the appropriate BRIEF/PLAN shape.
- If you output a BRIEF/PLAN, archive the same content with
  `python scripts/save_report.py ...`; do not rely on chat output alone.

If you must hand off to another model or session, follow the 4-section
Handoff Protocol defined in AGENTS.md §13.

Finish with changed files, verification result, and remaining risks.
```
