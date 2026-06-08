# Owner

## 역할 정의

Owner는 **사람(사용자)** 이다. 에이전트가 아니며 worker 로 동작하지 않는다.
프로젝트의 최종 권한을 가지지만, 일상 운영은 CEO 에이전트에 위임하고
**큰 결정만** 직접 판단한다.

핵심 의도: **Owner 에게 가는 보고를 최소화하고, CEO 에이전트가 최대한
자율적으로 판단**하게 한다. Owner 는 모든 진행을 보고받지 않는다.

## 핵심 책임

- **최종 승인**: 큰 방향 전환, 역할/워크플로 변경, 비용·리스크가 큰 결정, 파일 삭제·롤백 같은 파괴적 작업의 최종 승인.
- **충돌 해소**: CEO 에이전트가 에스컬레이션한 충돌·교착의 결정.
- **비전 설정**: 장기 방향과 우선순위의 큰 틀 제시(세부는 CEO 위임).

## Owner 가 직접 보는 것 (에스컬레이션 기준)

CEO 에이전트는 다음만 Owner 에게 올린다. 그 외는 CEO 가 자율 처리한다.

- 큰 방향 전환 / 제품 스코프의 중대한 변경
- 역할·워크플로·운영 규칙의 구조적 변경
- 되돌리기 어렵거나 외부에 공개되는 작업 (배포, 외부 전송)
- 파일/디렉터리 삭제, recursive move/delete, rollback, `git reset --hard`, `git checkout --`, force push
- secret/credential 접근·회전, 운영 DB 변경, 데이터 손실 가능 작업
- 치명 결함 발견 후 방향 전환 또는 중단 결정
- 비용/리스크가 임계 초과 (TOKEN-BUDGET·safety_gate 기준)
- CEO 가 자율 판단 불가로 명시한 교착

`audience: Owner` 로 표시된 BRIEF/PLAN 만 Owner 를 향한다.
나머지(`audience: CEO`)는 CEO 에이전트가 수신한다.

일상적인 `bash`/`python`/`git` 조회·검증·비파괴 편집 결정은 CEO가 흡수한다.
다만 Codex/Claude 실행 플랫폼이 보안상 Owner 승인 프롬프트를 강제하면 저장소
정책만으로 우회하지 않는다. 이때도 에이전트는 필요한 최소 명령과 이유만 올린다.

## 경계

- Owner 는 worker 가 아니다 — orchestrator 는 owner 를 자동 호출하지 않는다.
- 구현·작업 분장·감사 판정을 직접 하지 않는다(각각 팀/Lead/Auditor).
- Owner 가 직접 지시하면 그 지시가 최우선이며, CEO/Lead 는 이를 따른다.

## 관계

```
Owner (사람)
 └─ CEO (자율 에이전트)   ← routine 자율 처리, 큰 건만 Owner 에스컬레이션
      └─ Lead Engineer
           └─ 실무/조율/감사/문서/근거 역할들
```

자세한 구분은 [AGENTS.md §3.4](../../AGENTS.md), CEO 역할은
[agents/ceo/SKILL.md](../ceo/SKILL.md) 참조.
