# GEMINI.md

Gemini-specific companion guidance for this repository.

## Documentation Order

Before non-trivial work, read:

1. `AGENTS.md`
2. `README.md`
3. `AGENT_RUNTIME.md`
4. `agents/lead_engineer/STATUS.md`
5. `agents/roles.yml`
6. `CLAUDE.md`
7. `GEMINI.md`
8. the relevant `agents/{role}/SKILL.md`
9. the active `TASK` or backlog item

`AGENTS.md` is the shared source of truth.

## Core Mandates

1. Think before coding.
2. Keep changes minimal and task-scoped.
3. Ask only when ambiguity blocks safe progress.
4. Preserve user changes.
5. Verify before closure.
6. Record results in the task or report system.

## Multi-Agent Workflow

The repository uses a role-based operating model:

```text
Owner -> CEO -> Lead Engineer -> role workers and reviewers
```

Owner is the human final authority for high-risk boundaries. CEO absorbs
routine coordination. Lead Engineer plans and closes tasks. Specialist roles
contribute evidence, implementation, review, or cleanup according to
`agents/roles.yml`.

## Technical Guidance

- Reuse existing scripts and tests.
- Do not hard-code local machine paths.
- Do not store secrets in the repository.
- Do not publish generated runtime state.
- Run focused tests first, then the repository gate.

## Reporting

Use BRIEF format for final status:

```text
Bottom Line: <result>.
Signal: <evidence>.
Insight: <interpretation>.
Decision: <needed decision or none>.
```
