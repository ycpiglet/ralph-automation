# Agent Bootstrap Prompts

이 폴더는 Codex, Claude, Gemini, Cursor 같은 여러 LLM/에이전트가 같은 시작 절차를 밟도록 만드는
부트스트랩 프롬프트 예시를 담는다.

모든 프롬프트는 아래 공통 전제를 가진다.

1. [AGENTS.md](../../AGENTS.md)를 저장소 공통 운영 규칙으로 먼저 읽는다.
2. [README.md](../../README.md)와 도구별 문서를 이어서 읽는다.
3. `agents/lead_engineer/CYCLE-*.md` 중 파일명 숫자가 가장 큰 최신 사이클을 찾는다.
4. 관련 TASK/BTC/BUG를 확인해서 중복 작업을 만들지 않는다.
5. 작업 전 Context Snapshot을 만들고, 완료 후 검증 결과와 인수 사항을 남긴다.

## 예시 목록

- [Codex](codex.md)
- [Claude](claude.md)
- [Gemini](gemini.md)
- [Cursor](cursor.md)
- [Scheduled prompt templates](scheduled_prompts.md)

새 모델이나 에이전트가 추가되면 이 폴더에 같은 형식의 예시를 추가한다.
