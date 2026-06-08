# Role Skill Structure

이 저장소의 역할 문서는 `agents/{role}/SKILL.md` 를 canonical entrypoint 로 유지한다.
다만 세부 지식과 반복 실행 절차는 progressive disclosure 구조로 분리한다.

## Standard Layout

```text
agents/{role}/
├── SKILL.md                 # 필수: 역할 개요, trigger, 핵심 절차, 어떤 파일을 언제 읽을지
├── GOTCHAS.md               # 선택: 반복 실수, 절대 피해야 할 함정, 짧은 교정 규칙
├── troubleshooting.md       # 선택: 흔한 에러, 진단 순서, known warning
├── references/
│   ├── api.md               # 선택: API/schema/contract 등 상세 참조
│   ├── workflow.md          # 선택: 긴 절차·예시·decision table
│   └── examples.md          # 선택: 좋은 산출물/나쁜 산출물 예시
├── scripts/                 # 선택: 역할 전용 deterministic helper
└── assets/                  # 선택: 템플릿, 이미지, 샘플 산출물 등 output resource
```

루트 `skills/` 는 현재 만들지 않는다. 이 repo 의 실행 단위는 `agents/{role}` 이며,
Codex/Claude skill 표준은 그 내부 구조 설계 원칙으로 적용한다.

## Loading Contract

1. `SKILL.md` 는 항상 얇게 유지한다. 목표는 500 lines 이하이며, 세부 내용은 참조 파일로 보낸다.
2. `SKILL.md` 는 모든 optional resource 를 직접 링크하고, **언제 읽을지**를 한 줄로 설명한다.
3. 참조는 한 단계만 둔다. `SKILL.md → references/foo.md → references/bar.md` 처럼 중첩 참조하지 않는다.
4. 100 lines 를 넘는 reference 는 상단에 `## Contents` 를 둔다.
5. 반복해서 같은 코드를 쓰거나 fragile 한 절차는 `scripts/` 로 내린다.
6. role-local evidence 는 각 역할 디렉터리에 두고, TASK 상태와 완료 기록은 중앙 `agents/lead_engineer/tasks/` 에 유지한다.

## File Responsibility

| File/dir | Put here | Do not put here |
|----------|----------|-----------------|
| `SKILL.md` | 역할 정의, trigger, core workflow, resource navigation | 긴 API docs, 과거 사건 전문, 대형 예시 |
| `GOTCHAS.md` | 재발 실수와 짧은 방지 규칙 | 일반 가이드, 전체 회고 전문 |
| `troubleshooting.md` | 에러 메시지, 진단 순서, known warnings | 정상 workflow 설명 |
| `references/*.md` | API/schema/checklist/examples/long decisions | 다른 reference 로만 이어지는 nested index |
| `scripts/*` | deterministic helper, report, validator | 일회성 실험, secret 포함 코드 |
| `assets/*` | 템플릿/이미지/샘플 | 읽어야 하는 설명 문서 |

## Migration Rule

기존 역할 문서를 한 번에 쪼개지 않는다. 다음 조건 중 하나일 때만 분리한다.

- `SKILL.md` 가 500 lines 에 접근한다.
- 같은 API/schema/명령 설명을 두 번 이상 재작성한다.
- COMPOUND/GOTCHA 가 한 역할에 누적돼 bootstrap 비용을 올린다.
- 역할 완료 증거가 중앙 TASK 본문을 불필요하게 부풀린다.

분리할 때는 먼저 `SKILL.md` 에 resource navigation 을 추가하고, 그 다음 세부 내용을 새 파일로 이동한다.
파일 이동이 링크를 많이 바꾸면 별도 migration TASK 로 분리한다.

## Validation

구조 점검은 read-only 로 실행한다.

```powershell
python scripts/check_skill_structure.py
```

이 검사는 현재 warning/report 용이다. 모든 역할에 optional 파일을 강제하지 않는다.
강제는 Phase 2에서 역할별 evidence template 이 들어간 뒤 `check_agent_docs.py` 로 승격한다.
