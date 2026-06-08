# Cursor Bootstrap Prompt

```text
이 저장소에서 Cursor 또는 IDE 기반 에이전트로 작업한다.

작업 전 AGENTS.md를 최우선 규칙으로 읽는다.
그 다음 README.md, AGENT_RUNTIME.md, agents/lead_engineer/STATUS.md, agents/lead_engineer/AUDIT-LOG.md, agents/roles.yml, CURSOR.md, 최신 agents/lead_engineer/CYCLE-*.md, 본인 역할의 최신 agents/{role}/retros/RETRO-{role}-*.md (있으면), 관련 REVIEW, tasks/INDEX.md, 관련 TASK/BTC/BUG를 확인한다.
`.cursor/rules`가 없으면 있다고 가정하지 않는다.

Context Snapshot을 만든 뒤 작업한다.
- 최신 사이클
- 현재 요청
- 관련 TASK/BTC/BUG
- Owner
- 작업 범위
- 제외 범위
- 완료 기준
- 검증 방법

파일 자동 수정이나 대량 리팩터링은 현재 TASK 범위 안에서만 수행한다.
같은 목적의 TASK/BTC/BUG를 새로 만들지 않는다.
완료 후 TASK 상태와 결과 기록을 갱신하고, 문서 링크와 사이클 참조를 확인한다.

문서/에이전트 규칙을 바꾼 경우 `python scripts/check_agent_docs.py` 를 실행해 0 errors 를 확인한다.
인계가 필요하면 AGENTS.md §13 Handoff Protocol 4단 구조를 따른다.
```
