# Scheduled Prompt Templates

Codex Automations / Claude Code scheduled tasks에 넣을 수 있는 저빈도 프롬프트 템플릿이다.
목표는 LLM을 상시 깨워두지 않고, 정해진 시각에만 같은 기준으로 repo 상태를 정리하게 하는 것이다.

## 공통 원칙

- 대기 중 local daemon은 LLM을 호출하지 않는다. LLM scheduled task는 실행 시점에만 사용량을 쓴다.
- first line은 반드시 `Bottom Line:`으로 시작한다.
- 보고는 `Bottom Line -> Signal -> Insight -> Decision` 순서로 작성한다.
- source of truth는 `agents/lead_engineer/tasks/BACKLOG.md`, `schedule_runs/latest.md`, `agents/lead_engineer/STATUS.md`, `agents/lead_engineer/AUDIT-LOG.md`다.
- 파괴적 작업, `auto_runner --execute`, production deploy, secret, prod DB, 파일 삭제/롤백은 하지 않는다.
- 새 작업을 만들지 않는다. 새 작업 후보는 "제안"으로만 적고, TASK 생성은 별도 Owner 지시가 있을 때만 한다.

## Codex Automation — Morning Owner Brief

권장 주기: 매일 08:30 KST.

```text
Return to this repository conversation every weekday morning and prepare a concise Owner-facing morning brief.

Read these first:
1. AGENTS.md
2. agents/lead_engineer/tasks/BACKLOG.md
3. schedule_runs/latest.md
4. agents/lead_engineer/STATUS.md
5. agents/lead_engineer/AUDIT-LOG.md

Do not edit files, run destructive commands, deploy, access secrets, run prod DB writes, or run `auto_runner --execute`.
If the local schedule daemon is relevant, report its latest heartbeat only if visible from context or a safe local status command is available.

Output format:
Bottom Line: <open work count, ACT/ASK/DEFER shape, and one recommended focus>.

## Signal
| Item | State | Evidence |
|------|-------|----------|
| Backlog | G/Y/R | <ACT/REVIEW/ASK/DEFER count> |
| Schedule | G/Y/R | <latest schedule report or daemon heartbeat> |
| Due Checks | G/Y/R | <doc/beta/scribe signals if available> |

## Insight
1. <What changed since the last brief, or "no material change".>
2. <What decision bottleneck matters today.>

## Decision
1. <Only if Owner decision is needed. Otherwise write "없음".>
```

## Claude Code Scheduled Task — Session Check-in

권장 주기: 작업 세션 중 2-4시간마다. Claude Code scheduled tasks는 세션이 실행 중이고 idle일 때만 안정적으로 동작한다.

```text
In this Claude Code session, check whether the repo-local schedule and backlog state needs attention.

Read:
1. AGENTS.md
2. CLAUDE.md
3. agents/lead_engineer/tasks/BACKLOG.md
4. schedule_runs/latest.md

If safe local commands are available, run only read-only checks:
- python scripts/local_schedule_daemon.py status
- python scripts/doc_steward_due.py --quiet
- python scripts/beta_tester_due.py --quiet
- python scripts/scribe_due.py --quiet

Do not edit files unless the user explicitly asked this scheduled task to maintain docs.
Do not run model-expensive live smoke, production writes, deployment, secret access, or full-auto execution.

Report in Mini BRIEF:
Bottom Line: <healthy / attention needed / blocked>.
Signal: <daemon heartbeat, due-checks, backlog lane>.
Decision: <none, or exact Owner gate>.
```

## Claude Code Cloud / Routine — Weekly Review Candidate

권장 주기: 주 1회. 로컬 세션이 꺼져도 실행되어야 하는 경우에만 사용한다.

```text
Prepare a weekly repository governance review for this project.

Use the repository documents as source of truth:
- AGENTS.md
- agents/lead_engineer/tasks/BACKLOG.md
- agents/lead_engineer/STATUS.md
- agents/lead_engineer/AUDIT-LOG.md
- agents/lead_engineer/reviews/REVIEW-*.md
- agents/lead_engineer/reports/INDEX.md

Do not change files, deploy, access secrets, or run destructive commands.
Summarize only:
1. what is still blocked by Owner/external gates,
2. which automation or reporting loops are healthy,
3. which repeated issues should become a Compound candidate,
4. which one low-risk next action is worth doing.

Output as Standard BRIEF, first line `Bottom Line:`.
```

## Local Non-LLM Commands

로컬 deterministic 상태 확인은 scheduled LLM task 대신 이 명령을 사용한다.

```powershell
python scripts/local_schedule_daemon.py status
python scripts/local_schedule_daemon.py tick --force
python scripts/doc_steward_due.py --quiet
python scripts/beta_tester_due.py --quiet
python scripts/scribe_due.py --quiet
```
