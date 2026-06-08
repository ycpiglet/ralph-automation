# Gemini Bootstrap Prompt

```text
이 저장소에서 Gemini 계열 에이전트로 작업한다.

먼저 AGENTS.md를 공통 운영 규칙으로 읽고, README.md, AGENT_RUNTIME.md, agents/roles.yml, GEMINI.md를 이어서 읽는다.
그 다음 agents/lead_engineer/STATUS.md, AUDIT-LOG.md, compound_log.md, 관련 REVIEW, tasks/INDEX.md를 확인한다.
본인 역할의 가장 최근 RETRO (agents/{role}/retros/RETRO-{role}-*.md, 있으면)도 함께 확인한다 — §5 Forward Actions가 다음 행동의 입력.
문서에 고정된 사이클 번호가 있더라도 현재 사이클로 가정하지 않는다.
항상 agents/lead_engineer/CYCLE-*.md 중 파일명 숫자가 가장 큰 파일을 최신으로 판단한다.

작업을 시작하기 전 Context Snapshot 을 내부적으로 만든다.
- 최신 사이클
- 현재 요청
- 관련 TASK/BTC/BUG
- Owner
- 작업 범위와 제외 범위
- 완료 기준
- 검증 방법

추가로 확인한다.
- 관련 TASK/BTC/BUG가 이미 있는가?
- 최신 CYCLE 목표에 포함되는가?
- Owner와 완료 기준이 명확한가?
- 검증 방법이 문서화되어 있는가?

불명확한 경우 임의로 구현하지 말고 Lead Engineer 기록 또는 사용자에게 확인할 항목으로 남긴다.
구현 후에는 TASK 상태, 결과 기록, 검증 결과를 갱신한다.
문서/에이전트 규칙을 바꾼 경우 python scripts/check_agent_docs.py를 실행한다.
인계가 필요하면 AGENTS.md §13 Handoff Protocol 4단 구조를 따른다.
```
