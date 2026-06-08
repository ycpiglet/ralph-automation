# AGENTS.md

Shared operating protocol for Codex, Claude, Gemini, Cursor, and other agents
working in this repository.

This file is the repository-level source of truth. Tool-specific files such as
`CLAUDE.md`, `GEMINI.md`, and `CURSOR.md` may add local guidance, but when they
conflict, follow this file and the latest records under `agents/lead_engineer/`.

## 0. Core Rules

1. Read the same starting context before doing non-trivial work.
2. Do not start implementation without an explicit user request or an approved
   `CYCLE` / `TASK` record.
3. Do not create duplicate work. Search existing tasks and backlog first.
4. Keep changes small, scoped, and verifiable.
5. A task is not complete until the result and verification are recorded.
6. Do not guess timestamps. Use `python scripts/now.py`.
7. Never commit secrets, credentials, private runtime state, or local tool data.

## 1. Start Protocol

Before non-trivial work, read:

1. `AGENTS.md`
2. `README.md`
3. `AGENT_RUNTIME.md`
4. `agents/lead_engineer/STATUS.md`
5. `agents/lead_engineer/AUDIT-LOG.md`
6. `agents/roles.yml`
7. Tool-specific guidance, if relevant
8. Your role file: `agents/{role}/SKILL.md`
9. `agents/lead_engineer/tasks/BACKLOG.md`
10. The latest relevant `CYCLE`, `REVIEW`, and `TASK` files

Create an internal context snapshot:

```text
latest cycle:
current request:
related task:
role:
owner:
scope:
out of scope:
done when:
verification:
```

Only print the snapshot when it would clarify ambiguity or risk.

## 2. Source Of Truth

| Topic | Source |
|-------|--------|
| Project overview and setup | `README.md` |
| Shared agent protocol | `AGENTS.md` |
| Current operating status | `agents/lead_engineer/STATUS.md` |
| Decisions and operating changes | `agents/lead_engineer/AUDIT-LOG.md` |
| Role registry | `agents/roles.yml` |
| Open work board | `agents/lead_engineer/tasks/BACKLOG.md` |
| Task registry | `agents/lead_engineer/tasks/INDEX.md` |
| Individual tasks | `agents/lead_engineer/tasks/TASK-*.md` |
| Reports | `agents/lead_engineer/reports/` |
| Runtime model | `AGENT_RUNTIME.md` |

Do not hard-code the current cycle number in tool-specific documents. Determine
the latest cycle from the files in `agents/lead_engineer/`.

## 3. Roles

| Role | Responsibility |
|------|----------------|
| Owner | Human final authority for irreversible, external, destructive, or high-risk changes |
| CEO | Autonomous coordinator for routine goals, scope, priority, cost, and risk |
| Managing Partner | Independent cost, direction, and role-balance review |
| Lead Engineer | Plan, task definition, assignment, review, and closure records |
| Independent Auditor | Evidence, completion, cost, and self-review audit |
| Doc Steward | Documentation freshness, integrity, stale references, and missing artifacts |
| Scribe | Cleanup, compression, normalization, and archive notes after canonical state is set |
| Research Agent | Evidence notes from official docs, standards, or external examples |
| Timeline Agent | Chronology reconstruction across tasks, meetings, audits, and messages |
| Requirements Interviewer | Clarifies ambiguous requests before plan or implementation |
| Secretary | Personal desk summary, reminders, agenda prep, and non-governance assistance |
| Backend Engineer | Server, data, auth, and API surfaces for the host project |
| UI/UX Designer | Frontend, accessibility, responsive behavior, and user workflows |
| CI/CD Engineer | Git, PR, release, deployment, and environment workflows |
| QA | Tests, regression checks, bug reports, and quality gates |
| Beta Tester | User-perspective exploration and scenario reports |

One task has one accountable owner. Collaborators may contribute, but the owner
closes the record.

## 4. Work Selection

When a request arrives:

1. Check whether an existing `TASK`, bug, or test case already covers it.
2. If yes, continue that record instead of creating a duplicate.
3. If it is inside the current cycle, work under that cycle.
4. If it is a direct user request outside the current cycle, keep it small and
   record it.
5. If the request changes architecture, workflow, public release, secrets, data,
   or irreversible state, escalate before mutation.

Allowed task states:

- `대기`
- `진행 중`
- `완료`
- `보류`

## 5. Reversibility Gate

Use reversibility and blast radius to decide whether to act or ask.

| Level | Rule |
|-------|------|
| R1 | Reversible and in scope: act, verify, record |
| R2 | Reversible but slightly ambiguous or cross-scope: act, flag assumptions and undo path |
| R3 | Irreversible, destructive, external, secret-bearing, production-data, or high-risk: ask Owner |

Examples that require Owner approval:

- file or directory deletion
- recursive move or delete
- force push, rollback, hard reset, or checkout that discards work
- production deployment or external publication
- secret access or rotation
- production data writes
- disabling safety checks

If the execution platform asks for permission, do not bypass it. Present the
smallest safe command scope.

## 6. File Edits

1. Check the worktree before edits.
2. Preserve user changes.
3. Edit only files tied to the current task.
4. Keep generated, local, runtime, and secret files out of public release.
5. Use structured parsers or existing scripts where available.
6. If behavior changes, update the relevant docs and verification records.

## 7. Records

New task records should include:

```yaml
---
type: task
id: TASK-NNN
status: 대기
owner: Lead Engineer
assignees: [Lead Engineer]
priority: Medium
difficulty: 중
est_hours: 1
est_tokens: 10000
tags: [automation]
trigger_meeting: 자가발생
audit_log: AUDIT-YYYY-MM-DD-NNN
created: YYYY-MM-DD
created_at: YYYY-MM-DDTHH:MM:SS+09:00
---
```

Completion records must state:

- original request
- actual work performed
- result
- changed files
- verification commands and outcomes
- remaining issues or handoff notes

## 8. Reporting

Final task reports use BRIEF format:

```text
Bottom Line: <one-line status and decision>.

## Signal
| Item | State | Evidence |
|------|-------|----------|
| Work | G/Y/R | <evidence> |

## Insight
1. <short interpretation>

## Decision
1. <decision needed, or "없음">
```

Use `G`, `Y`, and `R` for state. Do not use emoji.

## 9. Time

Use these commands for timestamps:

```powershell
python scripts/now.py
python scripts/now.py --utc
python scripts/now.py --date
```

If Python is unavailable, mark time as `unknown` rather than guessing.

## 10. Git

Default flow:

1. Create a task branch.
2. Make small scoped commits.
3. Run focused checks.
4. Open a PR or follow the repository's release plan.
5. Merge only through the configured gate.

Do not push directly to `main` unless this repository explicitly allows it.

## 11. Ralph Sync

This repository may consume reusable automation through `ralph-automation`.
Host projects pin the upstream in `ralph.yml` and update through:

```powershell
ralph update-plan --root . --install-dir .tmp/ralph-upstream --check
ralph update --root . --install-dir .tmp/ralph-upstream --check
ralph update --root . --install-dir .tmp/ralph-upstream --diff
ralph update --root . --install-dir .tmp/ralph-upstream --apply
ralph lock --root . --write
```

The update path must preserve host edits and only apply managed template files.

## 12. Validation

Before closure, run the narrowest useful checks first, then the repository gate:

```powershell
python scripts/check_agent_docs.py
```

If a check cannot run, report exactly why and what remains unverified.
