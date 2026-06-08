# Claude Bootstrap Prompt

```text
이 저장소에서 Claude 계열 에이전트로 작업한다.

작업 전 반드시 다음 문서를 순서대로 읽는다.
1. AGENTS.md
2. README.md
3. AGENT_RUNTIME.md
4. agents/lead_engineer/STATUS.md
5. agents/lead_engineer/AUDIT-LOG.md
6. agents/roles.yml
7. CLAUDE.md
8. agents/{role}/SKILL.md
9. agents/lead_engineer/compound_log.md
10. agents/lead_engineer/CYCLE-*.md 중 파일명 숫자가 가장 큰 최신 사이클
11. agents/{role}/retros/RETRO-{role}-*.md 중 최신 (본인 역할의 직전 회고, 있으면)
12. 관련 agents/lead_engineer/reviews/REVIEW-*.md
13. agents/lead_engineer/tasks/INDEX.md
14. 관련 TASK/BTC/BUG 문서

Context Snapshot을 내부적으로 만든다.
- 최신 사이클
- 현재 요청
- 관련 TASK/BTC/BUG
- 담당 역할과 Owner
- 작업 범위와 제외 범위
- 완료 기준
- 검증 방법

같은 목적의 TASK가 있으면 새 번호를 만들지 말고 기존 TASK를 이어받는다.
TASK 상태는 대기, 진행 중, 완료, 보류 중 하나만 사용한다.
완료 시에는 TASK 결과 또는 관련 기록에 변경 파일, 검증, 이슈, 인수 사항을 남긴다.

문서/에이전트 규칙을 바꾼 경우 `python scripts/check_agent_docs.py` 를 실행해 0 errors 를 확인한다.
인계가 필요하면 AGENTS.md §13 Handoff Protocol 4단 구조를 따른다.

스케줄/상시 실행 경계:
- OS Task Scheduler 상태는 `python scripts/schedule_task.py status` 로 확인한다.
- Windows Task Scheduler가 `LastTaskResult=255` 이거나 현재 세션에서 OS task가 보이지 않으면
  자동화 완료로 주장하지 않는다.
- local fallback은 `python scripts/local_schedule_daemon.py status`,
  즉시 R1 smoke는 `python scripts/local_schedule_daemon.py tick --force`,
  상시 루프는 `python scripts/local_schedule_daemon.py watch --interval 60 --run-now` 를 사용한다.
- Claude slash command는 `/schedule-local` 로 이 경로를 안내한다.
- local fallback 자체는 대기 중 LLM을 호출하지 않는다. 현재 활성 notify 스케줄은
  `maintenance`/`digest` 읽기 전용 스크립트만 실행한다.
- Claude Code scheduled tasks는 Claude Code가 실행 중이고 idle일 때만 fire 되는
  세션 스코프 기능이다. 매일/매주 같은 prompt를 Claude가 다시 판단해야 하는
  저빈도 작업에만 사용하고, repo-local 상태판은 deterministic fallback을 우선한다.
```
