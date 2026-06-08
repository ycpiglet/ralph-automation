# Ralph Prompt Scaffold — mode별 입력·출력 계약 (TASK-NNN)

작성일: 2026-05-27
Owner: Lead Engineer (Managing Partner 위임)
참조: [TASK-NNN](tasks/TASK-NNN-ralph-prompt-scaffold.md), [scripts/agent_loop.py](../../scripts/agent_loop.py) (5 모드), [agents/roles.yml](../roles.yml) (역할 입출력 계약), [docs/agent_bootstrap/](../../docs/agent_bootstrap/)

---

## 0. 목적

Ralph 식 "매 반복 prompt 파일을 읽어 fresh context 구성" 을 본 프로젝트 역할 체계에 맞게 mode별 scaffold 로 연결한다. `agent_loop.py` 의 5 모드(plan/build/review/audit/retro)가 각각 **무엇을 읽고(필수 입력), 무엇을 안 읽고(금지 입력), 무엇을 남기는지(출력 계약)** 를 고정한다.

본 문서는 *조립 규칙* 이다. 실제 prompt 텍스트는 mode + 역할 + 현재 TASK 로 런타임에 조립된다 (loop runner / agent_context_packet.py).

## 1. 공통 입력 (모든 mode)

매 반복 fresh 로 읽는 베이스 (AGENTS.md §1 시작 프로토콜 요약):
- `AGENTS.md`, `agents/lead_engineer/STATUS.md`, 최신 `CYCLE-*.md`
- 역할별 `agents/{role}/SKILL.md`, `agents/roles.yml` 의 해당 role required_inputs

## 2. mode별 scaffold

### plan (read-only 제안)

| 항목 | 값 |
|------|-----|
| 필수 입력 | STATUS.md, tasks/INDEX.md, 최신 CYCLE, RETRO §5 Forward |
| 금지 입력 | 구현 디테일(코드 본문) — 계획 단계라 불필요 |
| 출력 계약 | 다음 TASK 제안 (우선순위·범위·완료기준), 상태 변경 없음 |
| agent_loop | `run_mode_plan` (read-only) |

### build (구현)

| 항목 | 값 |
|------|-----|
| 필수 입력 | 대상 TASK 본문, 관련 코드/파일, roles.yml output_contract |
| 금지 입력 | 무관한 TASK 의 진행 맥락 (범위 오염 방지) |
| 출력 계약 | 코드 변경 + 완료 기록 + 검증 출력. 협업 등급(§16) 적용 |
| agent_loop | `run_mode_build` |

### review (검토)

| 항목 | 값 |
|------|-----|
| 필수 입력 | 변경 diff, 완료 기준, 검증 출력. **기본 컨텍스트 T0(diff-only)** — COLLAB-CONTEXT-STRATEGY |
| 금지 입력 | 구현자의 사유·미래 계획 (현재 증거 기반) |
| 출력 계약 | VERDICT(approve/needs-changes) + 근거. 신호 시 tier 에스컬레이션 |
| agent_loop | `run_mode_review` |

### audit (독립 감사)

| 항목 | 값 |
|------|-----|
| 필수 입력 | git diff, 검증 명령 출력, AGENTS.md §6.4 Gate 기준 |
| **금지 입력** | **구현자의 전체 대화 맥락·사유·토론 (편향 방지 — 가장 엄격)**, 예측·추측 |
| 출력 계약 | 판정(통과/보류/재검토 필요) + 근거 5종 + 해소 조건 |
| agent_loop | `run_mode_audit` |

> audit mode 는 구현자 맥락에 *끌리지 않도록* 입력이 가장 제한된다. 단일 세션 환경(§15.7)에서는 subagent(auditor) 분리 호출로 충족.

### retro (회고)

| 항목 | 값 |
|------|-----|
| 필수 입력 | 사이클 TASK 완료 기록, 추정 vs 실측, 직전 RETRO |
| 금지 입력 | 개인 비난 맥락 (blameless) |
| 출력 계약 | 5섹션 RETRO (§1 Planned vs Actual … §5 Forward Actions) |
| agent_loop | `run_mode_retro` |

## 3. Ralph 명칭 ↔ 본 프로젝트 매핑

| Ralph 파일 | 본 프로젝트 대응 |
|------------|------------------|
| PROMPT.md | mode scaffold (본 문서) + agent_context_packet 조립 |
| SYSTEM.md | AGENTS.md + agents/roles.yml + 역할 SKILL.md |
| MEMORY.md | STATUS.md + AUDIT-LOG.md + compound_log.md + meetings/ |
| LOG.md | agents/runtime/events/ + agents/messages/ + agents/runtime/safety_violations/ |
| PLAN.md / IMPLEMENTATION_PLAN.md | 최신 CYCLE-*.md + tasks/INDEX.md + tasks/TASK-*.md + generated BACKLOG.md |

별도 PROMPT/SYSTEM/MEMORY 파일을 새로 만들지 않는다 — 기존 문서 체계로 매핑해 source-of-truth 중복(drift)을 피한다 (MEETING-YYYY-MM-DD-NNN / TASK-NNN 원칙).
정확한 canonical/generated/projection inventory와 drift rule은 [specs/agent_loop/ralph_compatibility.md](../../specs/agent_loop/ralph_compatibility.md)를 따른다.

## 4. 조립 규칙 (loop runner)

```text
prompt = 공통입력(§1)
       + mode_scaffold[mode] (필수입력 로드, 금지입력 제외)
       + 현재 TASK 본문 (build/review/audit)
       + 출력 계약 명시
```

audit mode 는 금지 입력(구현자 맥락)을 조립에서 *제외* 하는 것이 핵심.

## 변경 이력

- 2026-05-27 — 초안 (TASK-NNN, 축소 재정의). agent_loop 5 모드 + roles.yml 입출력 계약 연결. Ralph 명칭 매핑으로 drift 방지. AUDIT-YYYY-MM-DD-NNN.
- 2026-06-07 — TASK-NNN closeout 보강. MEMORY/LOG/PLAN 매핑을 Ralph adapter spec과 동일하게 맞추고, 상세 inventory는 spec으로 위임.
