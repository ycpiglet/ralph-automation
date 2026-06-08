# Ralph Adapter Spec (TASK-NNN 정식안)

작성일: 2026-06-06
요청 시각: 2026-06-06T14:51:29+09:00
소유자: Managing Partner / Lead Engineer 협업
대상 TASK: [TASK-NNN](../../agents/lead_engineer/tasks/TASK-NNN-ralph-compatibility-spec.md)

## 목적

Ralph 원안의 핵심 파일/흐름을 그대로 복사하지 않고, 현재 운영 체계(`AGENTS.md` + task registry + review/audit 체계)를 source-of-truth로 두는 형태의 adapter 규격을 정식 정의한다.

본 문서는 실행 전 문서 합의용 기준으로만 사용한다. 실제 반복 실행/runner 수정은 [TASK-NNN](../../agents/lead_engineer/tasks/TASK-NNN-agent-loop-runner.md), 실행 안전장치 보강은 [TASK-NNN](../../agents/lead_engineer/tasks/TASK-NNN-orchestrator-safety-gate.md)와 연동한다.

## 1) Ralph 원문 파일 ↔ 현재 소스매핑

| Ralph 대상 | 본 프로젝트 대응 | 구현 포인트 |
|---|---|---|
| `AGENTS.md` | `AGENTS.md` | 최상위 규칙 집합 + 시작 프로토콜 + 역할/의사결정 규칙 |
| `SYSTEM.md` | `AGENTS.md` + `agents/roles.yml` + 각 `agents/*/SKILL.md` | SYSTEM은 통합 런타임 규칙이므로 기존 파일로 대체 |
| `PROMPT.md` (mode별) | `agents/lead_engineer/RALPH-PROMPT-SCAFFOLD.md` + `scripts/agent_context_packet.py` | mode별 contract + role별 required/forbidden/input contract |
| `PLAN.md`/`IMPLEMENTATION_PLAN.md` | `agents/lead_engineer/CYCLE-*.md`, `agents/lead_engineer/tasks/INDEX.md`, `agents/lead_engineer/tasks/TASK-*.md`, generated `tasks/BACKLOG.md` | 의사결정 이력 + 추적 가능한 TASK graph. 현재 상태는 TASK frontmatter가 원천, BACKLOG는 파생 보드 |
| `MEMORY.md` | `agents/lead_engineer/STATUS.md` + `agents/lead_engineer/AUDIT-LOG.md` + `agents/lead_engineer/compound_log.md` + `agents/lead_engineer/meetings/` | 운영 맥락/결정/분기 기록의 append-only 축 |
| `LOG.md` | `agents/runtime/events/` + `agents/messages/` + `agents/runtime/safety_violations/` | 이벤트 로그/메시지 추적/위반 증거의 3중 기록 |
| `loop.sh` | `scripts/agent_loop.py` (canonical) + optional thin wrappers | OS 독립성 확보를 위해 Python 러너를 canonical로 둠 |
| `specs/*` | `specs/agent_loop/` (지표 문서 집합) | 실행용 spec 은 projection 용도로만 사용 |

## 1.1) canonical / generated / projection 구분

Ralph adapter의 핵심 제약은 "새 파일 이름을 만들 수 있는가"가 아니라 "어느 파일이 최종 상태를 말할 권한을 갖는가"다. 아래 구분을 벗어나면 source-of-truth drift로 본다.

| 분류 | 파일/디렉터리 | 권한 | 금지 |
|---|---|---|---|
| Canonical policy | `AGENTS.md`, `agents/roles.yml`, `agents/*/SKILL.md`, `CLAUDE.md` 보조 절 | 역할·권한·시작 프로토콜·금지 범위 | Ralph 전용 `SYSTEM.md`로 중복 정의 금지 |
| Canonical work state | `agents/lead_engineer/CYCLE-*.md`, `reviews/REVIEW-*.md`, `tasks/TASK-*.md`, `tasks/INDEX.md` | 현재 작업·완료 기준·상태·이월. 현재 상태의 원천은 TASK frontmatter이고 `BACKLOG.md`는 그 파생 단일 포인터다. | `specs/agent_loop/`나 루트 `PLAN.md`에 현재 작업 상태를 별도 기록 금지 |
| Canonical evidence | `agents/lead_engineer/AUDIT-LOG.md`, `compound_log.md`, `meetings/`, `reports/`, `agents/messages/`, `agents/runtime/events/`, `agents/runtime/safety_violations/` | 결정·위반·메시지·검증·보고 증거 | 루트 `LOG.md`/`MEMORY.md`에 병렬 evidence ledger 생성 금지 |
| Canonical runner | `scripts/agent_loop.py`, `scripts/agent_orchestrator.py`, `scripts/orchestrator_safety_gate.py`, `scripts/auto_dispatch.py` | 실제 실행·라우팅·안전게이트 | `loop.sh`/`loop.ps1`에 로직 복제 금지(thin wrapper만 허용) |
| Mode contract | `agents/lead_engineer/RALPH-PROMPT-SCAFFOLD.md`, `scripts/agent_context_packet.py` | mode별 입력·금지 입력·출력 계약 | 역할별 prompt 계약을 루트 `PROMPT.md`로 재정의 금지 |
| Projection/spec | `specs/agent_loop/ralph_compatibility.md` 및 후속 `specs/agent_loop/*` | mapping·adapter 설계·검증 기준 설명 | 현재 상태·진행률·다음 작업의 source of truth가 될 수 없음 |
| Generated view | `tasks/BACKLOG.md`, `tasks/VIEW-*.md`, `reports/VIEW-*.md`, `scripts/INDEX.md` | 사람이 보는 파생 보드/색인 | 직접 편집 금지. 생성기와 canonical 입력을 수정해야 함 |

실행 규칙:

- loop가 다음 작업을 고를 때는 projection 문서가 아니라 `BACKLOG.md`와 TASK frontmatter를 읽는다.
- loop가 무엇을 했는지 남길 때는 `TASK` 완료 기록, `AUDIT-LOG`, `agents/messages/`, `agents/runtime/events/` 중 하나에 남긴다.
- spec 변경이 정책 의미를 바꾸면 관련 TASK/AUDIT에도 같은 결정을 기록한다.
- generated view가 stale이면 `scripts/generate_views.py`로 재생성한다. 직접 줄 편집은 금지한다.

### Canonical / projection / generated inventory

| Path | Type | Writer | Source inputs | Human edit? | Drift rule |
|---|---|---|---|---|---|
| `AGENTS.md` | canonical policy | Owner/Lead Engineer | 운영 결정, AUDIT, role contract | Yes, explicit policy change only | 정책 의미 변경 시 STATUS/AUDIT/TASK와 함께 닫음 |
| `agents/roles.yml` | canonical policy | Lead Engineer | role SKILL, registry contract | Yes | dispatch alias/skill path 변경 시 `check_agent_docs.py` 통과 필요 |
| `agents/*/SKILL.md` | canonical role contract | 해당 role owner/Lead Engineer | 역할 책임, forbidden scope | Yes | role 책임 변경은 roles.yml과 동시 갱신 |
| `agents/lead_engineer/tasks/TASK-*.md` | canonical work state | TASK owner | 사용자 요청, CYCLE/MEETING, 검증 결과 | Yes | 상태 변경 후 INDEX/BACKLOG/generated view 재생성 |
| `agents/lead_engineer/CYCLE-*.md` | canonical plan/review input | Lead Engineer | BACKLOG, TASK, MEETING | Yes | loop plan이 달라도 CYCLE/TASK가 우선 |
| `agents/lead_engineer/reviews/REVIEW-*.md` | canonical review | Lead Engineer + reviewer/auditor | TASK evidence, test output, collab evidence | Yes | 완료/이월 판단은 REVIEW와 TASK 완료 기록이 함께 있어야 함 |
| `agents/lead_engineer/AUDIT-LOG.md` | canonical evidence | Lead Engineer/Auditor | 운영 변경, high-risk decisions, verifier changes | Yes | 중요한 결정이 spec에만 있으면 미완료 |
| `agents/messages/` | canonical runtime evidence | orchestrator/worker/agent | `/call`, worker replies, handoff | Yes for triage, scripts preferred | `check_messages.py` 오류는 TASK/REVIEW closure 전 해소 |
| `agents/runtime/events/` | canonical runtime evidence | runtime scripts | loop/collab/worker events | Append-only preferred | events만으로 TASK 완료 주장 금지 |
| `agents/runtime/safety_violations/` | canonical safety evidence | safety gate | blocked/warned actions | Append-only preferred | safety evidence 발생 시 AUDIT/TASK에 해소 조건 기록 |
| `scripts/agent_loop.py` | canonical runner | Lead Engineer | TASK-NNN, RALPH scaffold, safety gate | Yes | shell wrapper에 로직 복제 금지 |
| `scripts/agent_orchestrator.py` | canonical router | Lead Engineer | roles.yml, messages schema, safety gate | Yes | message lifecycle와 safety gate 우회 금지 |
| `agents/lead_engineer/RALPH-PROMPT-SCAFFOLD.md` | canonical mode contract | Managing Partner/Lead Engineer | TASK-NNN, roles.yml, AGENTS.md | Yes | mode 입출력 변경 시 agent_loop/agent_context_packet 정합 확인 |
| `specs/agent_loop/ralph_compatibility.md` | projection/spec | Managing Partner/Lead Engineer | TASK-NNN, existing runtime assets | Yes | adapter agreement일 뿐 현재 실행 plan 아님 |
| `agents/lead_engineer/tasks/BACKLOG.md` | generated view | `scripts/generate_views.py` | TASK frontmatter | No, generator only | stale 시 재생성. 직접 편집 금지 |
| `agents/lead_engineer/tasks/VIEW-*.md` | generated view | `scripts/generate_views.py` | TASK frontmatter | No, generator only | generated artifact merge conflict 규율 적용 |
| `agents/lead_engineer/reports/VIEW-*.md` | generated view | `scripts/generate_report_views.py` | report files | No, generator only | report 저장 후 재생성/check |
| `loop.sh`, `loop.ps1` (있을 때) | thin wrapper | Lead Engineer/CI-CD | `scripts/agent_loop.py` CLI | Yes, wrapper only | wrapper는 Python runner 호출 외 로직 금지 |

Write authority rule:

- `scripts/agent_loop.py` may emit runtime events, heartbeat, and optional bounded dispatch results.
- `scripts/agent_loop.py` must not create or mutate canonical TASK state unless it does so through the same TASK/AUDIT/message paths used by the orchestrator.
- Loop output is evidence or proposal. It is never sufficient by itself to mark a TASK complete.

## 2) 메시지/상태 스키마(원안 기준)

원안의 순수 file mailbox 특성을 유지하되, 현재 구현 체계에 맞춰 아래 필드를 강제한다.

- Message: `id`, `from`, `to`, `task_id`, `intent`, `type`, `status`, `ts`
- 필수 lifecycle: `open -> claimed -> answered/blocked -> archived`
- 루프 반복 입력은 `/call` 요청(또는 equivalent) 기반으로 생성된 메시지에서만 확장
- 증거는 반드시 task/event/message/로그 연결값(`task_id`, `evidence` 링크) 포함

이 부분은 [TASK-NNN](../../agents/lead_engineer/tasks/TASK-NNN-orchestrator-message-bus.md) schema와 오직 동일 구조로 정합.

## 3) RUNNER/오케스트레이터 경계

- 반복 제어 루프: `scripts/agent_loop.py`
- 단일 명령 라우터: `scripts/agent_orchestrator.py`
- 공통 안전 게이트: `scripts/orchestrator_safety_gate.py`
- 메시지 버스: `agents/messages/` (frontmatter + lint)
- 권한 경계: role registry 기반 (`agents/roles.yml`)

두 엔진은 분리되어 동작한다.

- `agent_orchestrator.py`는 `/spawn`, `/call`, `/kill`, `/status`, `/inbox`의 명령 인터페이스를 책임.
- `agent_loop.py`는 `plan → build → review → audit → retro` mode 순회와 stop condition(반복, 실패 제한, dirty, emergency stop)을 책임.
- `agent_loop.py` 내부 iteration에서 실제 호출 대상이 생기면 orchestrator safety gate를 통과해야 한다.

## 4) 운영/검증 규칙

1. **source-of-truth 단일화**
   - `TASK`, `CYCLE`, `REVIEW`, `STATUS`, `AUDIT-LOG`, `compound_log`, `meeting`은 canonical.
   - `specs/`는 canonical이 아니라 adapter projection.

2. **모드별 입출력 제한**
   - plan: 읽기 중심(요약/선택), write 금지
   - build: 대상 TASK 산출물만 변경
   - review: diff/evidence 근거 기반 verdict
   - audit: 구현자 맥락 최소화, 감사를 위한 최소 입력
   - retro: plan/actual/gap/forward 5섹션

3. **안전/비가역성 경계**
   - destructive/git-cleanup/deploy/secret-access는 loop auto/dispatch from build mode에서 제외.
   - AGENTS.md R3 항목은 loop가 자동 실행·자동 재시도·자동 승급하지 않는다.
   - 동일 role/task 반복 실패는 중단 신호 + auditor escalation 후보로 처리한다.
   - 각 위반은 safety_violation JSON evidence로 남긴다.

4. **기록 요구**
   - 모든 시작/중단/요청/응답 이벤트는 `agents/messages/` 또는 event/log로 추적.
   - 문서/작업 변경 시 `TASK` 완료 기록, 검증 명령, 비용 필드는 누락 없이 기록.

## 4.0) loop safety hard stops

이 adapter는 "loop가 알아서 더 시도해 보면 되겠지"를 허용하지 않는다. 아래 조건은 `agent_loop.py`/`orchestrator_safety_gate.py`의 현재 상수와 맞춘 stop 기준이다.

| 조건 | 기본값/경로 | loop 동작 | evidence |
|---|---:|---|---|
| 기본 반복 수 | `max_iterations=1` | 1회 실행 후 정지 | `agents/runtime/events/agent_loop-{date}.jsonl` |
| 명시 목표 반복 기본 | `--goal`/`--explicit-auth`이면 5회 | bounded loop로만 승급 | event `safety_caps` |
| 반복 hard cap | `HARD_MAX_ITERATIONS=12` | 초과 요청은 12로 clamp | event `safety_caps` |
| 실패 hard cap | `max_failures=2` | 2회 실패 시 halt, 자동 재시도 중단 | loop event |
| dispatch hard cap | `HARD_DISPATCH_MAX=5` | 한 pass 최대 5건 | event/detail |
| dispatch token cap | `HARD_DISPATCH_SESSION_BUDGET=50000` | 초과 요청 clamp | event `safety_caps` |
| loop stop file | `agents/runtime/STOP_LOOP` | 다음 sleep/iteration 전 정지 | heartbeat/event |
| orchestrator stop file | `.orchestrator-stop` | spawn/call/build dispatch 차단 | safety evidence 가능 |
| dirty worktree | default stop | `--allow-dirty` 없으면 시작 전 정지 | loop stop reason |
| max active agents | `MAX_ACTIVE_AGENTS=5` | `/spawn` 차단 | `SAFETY-*.json` |
| max open messages | `MAX_OPEN_MESSAGES=20` | `/call` 차단 | `SAFETY-*.json` |
| max open per role | `MAX_OPEN_MESSAGES_PER_ROLE=5` | 해당 role `/call` 차단 | `SAFETY-*.json` |
| repeated same role/task | `REPEATED_FAILURE_THRESHOLD=3` | orchestrator는 warn, Ralph loop는 추가 자동 fan-out 금지 | `SAFETY-*.json` when emitted |
| call rate warning | `RATE_LIMIT_CALLS_PER_MINUTE=30` | warn, backoff 권고. loop는 같은 minute 추가 fan-out 금지 | `SAFETY-*.json` when emitted |

R3 hard stop:

- 파일/디렉터리 삭제, recursive move/delete, `git reset --hard`, `git checkout --`로 변경 폐기, rollback, force push, production deploy/external send, secret/credential 접근·회전, 운영 DB/데이터 손실 가능 작업, safety gate error 차단, 치명 결함 방향 결정은 loop가 즉시 중단하고 Owner/CEO 경계로 올린다.
- R3 또는 safety gate error는 자동 재시도하지 않는다. 다음 기록은 `blocked` 성격으로 남긴다: `task_id`, attempted action, matched policy/code, decision severity, evidence path, required human/external condition.
- no-progress pass(`dispatch: no open inbox work items`, 같은 후보 반복, 새 reply/event/file diff 없음)는 성공이 아니라 "소진/정지"다. 다음 후보는 BACKLOG 재평가 후 결정한다.

## 4.1) loop plan drift 방지 규칙

Ralph 원안의 `PLAN.md`/`LOG.md`를 이식하지 않는 대신, loop가 매 반복마다 아래 순서를 지켜 drift를 줄인다.

1. **Plan source refresh**
   - `AGENTS.md` 시작 프로토콜과 `BACKLOG.md`를 먼저 읽는다.
   - 최신 CYCLE/REVIEW는 파일명 숫자 최대값으로 판단한다.
   - `STATUS.md` narrative와 `BACKLOG.md`가 다르면 `BACKLOG.md`/TASK frontmatter를 우선하고, narrative는 정합화 대상이다.
   - loop가 생성한 plan/summary와 canonical TASK/CYCLE/AUDIT가 다르면 loop 출력은 proposal로만 취급하고, canonical 문서를 갱신하거나 loop 출력을 폐기한다.

2. **Mode-local write scope**
   - `plan` mode: 상태 변경 금지. 다음 후보와 가정만 제안.
   - `build` mode: 대상 TASK 산출물과 그 TASK 완료 기록만 변경.
   - `review` mode: diff/evidence 기반 verdict와 이월만 기록.
   - `audit` mode: 현재 증거와 비용/역할 독립성만 판단. 구현자의 미래 계획으로 완료를 승인하지 않음.
   - `retro` mode: forward action을 발견하되, 새 작업은 TASK로 승격되기 전까지 backlog가 아님.

3. **Drift stop condition**
   - TASK 상태와 `BACKLOG.md` decision lane이 어긋나면 `generate_views.py`/`check_agent_docs.py`를 먼저 실행한다.
   - open message가 stale/orphan이면 `check_messages.py` 또는 `agent_orchestrator.py status --json` 결과를 TASK/REVIEW에 기록한다.
   - dirty worktree에서는 `agent_loop.py` 기본 stop을 유지한다. 필요 시 `--allow-dirty`는 review/audit 같은 read-only 점검에만 사용한다.
   - R3 surface(secret, prod DB, destructive git, force push, external deploy, unattended execute)는 loop가 자동 승급하지 않는다.

4. **Projection freshness**
   - 본 spec은 상태판이 아니라 adapter mapping이다. TASK 상태·런타임 구현·message schema가 바뀌어 mapping이 틀리면 spec을 갱신하되, "현재 다음 작업"은 여기 쓰지 않는다.
   - drift 검증 최소 명령은 `check_agent_docs.py`, `check_messages.py`, 해당 runner/provider focused tests다.
   - completion cannot be claimed from loop output alone. Completion needs TASK 완료 기록, 검증 명령, and REVIEW/AUDIT evidence when required.

## 5) 구현 순서 제안 (TASK-NNN 실행 이후 선행/의존)

1. `TASK-NNN` 산출물(본 문서) 완성 → spec freeze
2. `TASK-NNN` mode scaffold 보강/정합 확인
3. `TASK-NNN` loop runner mode 구현(현재 dry-run 중심 + 안전 조건)
4. `TASK-NNN/086` 스키마·게이트 체계와의 실제 연동 증거 강화
5. `TASK-NNN` handoff/실무 전환 규약과 결합 후 `TASK-NNN` full run 확장

## 5.1) TASK-NNN~073 의존성 정리

TASK-NNN은 새 runner를 만드는 TASK가 아니라, 이미 생긴 runner/message/safety 자산을 Ralph adapter 관점에서 정합화하는 spec freeze 작업이다. 현재 의존성은 아래처럼 닫힌다.

| 관련 TASK | 현재 상태 | TASK-NNN에서의 의미 | 후속 필요 여부 |
|---|---|---|---|
| `TASK-NNN` agent loop runner | 완료 | `scripts/agent_loop.py`가 canonical loop 구현. 본 spec은 mode/SoT 기준을 제공 | 새 구현 없음. spec 변경 시 runner help/테스트 정합만 확인 |
| `TASK-NNN` agent message bus | 완료, `TASK-NNN`으로 흡수 | Ralph mailbox 요구는 `agents/messages/` + `check_messages.py`로 충족 | 새 mailbox 없음 |
| `TASK-NNN` prompt scaffold | 완료 | `RALPH-PROMPT-SCAFFOLD.md`가 루트 `PROMPT.md` 대체 | mode 계약 변경 시 이 문서와 동시 갱신 |
| `TASK-NNN` loop safety/observability | 완료, `TASK-NNN`으로 흡수 | stop file, forbidden intent, audit gate, safety evidence는 `orchestrator_safety_gate.py`와 `SAFETY-GATE.md` 기준 | 새 safety ledger 없음 |
| `TASK-NNN` orchestrator message bus | 완료 | message lifecycle/schema canonical | schema 변경 시 `check_messages.py`와 samples 갱신 |
| `TASK-NNN` orchestrator safety gate | 완료 | R3/forbidden/runaway 차단 canonical | loop가 safety gate를 우회하지 않도록 유지 |

따라서 TASK-NNN 완료 후 즉시 해야 할 일은 "새 파일 세트 생성"이 아니라, 이후 Ralph 관련 변경이 위 canonical 자산을 재사용하도록 하는 것이다.

### Dependency matrix

| TASK | depends_on | blocks | handoff artifact | acceptance signal |
|---|---|---|---|---|
| `TASK-NNN` runner | `TASK-NNN` mapping, `TASK-NNN` mode contract, `TASK-NNN` safety gate | Ralph loop execution changes | `scripts/agent_loop.py`, `scripts/test_agent_loop.py`, event log shape | `agent_loop --help`, mode dry-run/non-dry-run tests, safety caps/dirty-stop tests |
| `TASK-NNN` mailbox concept | `TASK-NNN` mapping, role handoff needs | Superseded by `TASK-NNN`; no direct new block | Historical TASK record pointing to `TASK-NNN` | `TASK-NNN` message schema/lint accepted; no new mailbox required |
| `TASK-NNN` prompt scaffold | `TASK-NNN` source-of-truth principle, `roles.yml` contracts | mode prompt/context assembly | `agents/lead_engineer/RALPH-PROMPT-SCAFFOLD.md` | 5 mode input/forbidden/output contracts exist and match `agent_loop.py` modes |
| `TASK-NNN` safety/observability concept | `TASK-NNN` loop risk boundaries, audit gate policy | Superseded by `TASK-NNN`; informs loop stop/audit rules | Historical TASK record pointing to `TASK-NNN`, `SAFETY-GATE.md` | safety gate policy/evidence exists; loop cannot bypass R3/forbidden/runaway checks |
| `TASK-NNN` message bus | `TASK-NNN` concept | orchestrator `/call`, worker replies, loop dispatch evidence | `agents/messages/README.md`, `scripts/check_messages.py` | message lifecycle lint 0 errors |
| `TASK-NNN` safety gate | `TASK-NNN` concept, orchestrator requirements | spawn/call/dispatch risk control | `scripts/orchestrator_safety_gate.py`, `SAFETY-GATE.md` | negative scenarios block/warn with evidence |

Closeout implication:

- `TASK-NNN~073` are not open dependencies for TASK-NNN. They are already implemented or superseded, and TASK-NNN now records how later Ralph work must reuse them.
- If a later Ralph change needs a new behavior outside this matrix, it must be a new TASK. It must not silently redefine TASK-NNN as an implementation task.

## 6) 이행 제한(옵션 C에서 유지)

- 본 작업은 지금은 **"Ralph을 복제하지 않고 adapter를 정의"**한다.
- 즉시 실행 가능한 "정식 래퍼 전환"은 요구하지 않고, 구현 추적성을 먼저 고정한다.
- 별도 `PROMPT.md`, `SYSTEM.md`, `MEMORY.md`, `LOG.md`, `PLAN.md`를 루트에 중복 생성하지 않는다.

## 7) 완료 판정 기준

TASK-NNN를 완료로 인정하려면 다음 네 가지가 모두 충족되어야 한다.

| 기준 | 충족 증거 |
|---|---|
| Ralph 파일 매핑이 존재 | 본 문서 §1 |
| canonical/generated/projection 구분이 명시 | 본 문서 §1.1 |
| plan drift 방지 규칙이 존재 | 본 문서 §4.1 |
| TASK-NNN~073 의존성이 정리됨 | 본 문서 §5.1 |

완료로 주장하지 말아야 할 것:

- Claude/Anthropic live provider가 동작한다는 주장.
- unattended scheduler/`auto_runner --execute`가 켜졌다는 주장.
- 새 `PROMPT.md`/`PLAN.md`/`LOG.md` 체계가 canonical로 생겼다는 주장.
- multi-iteration production loop가 안전하게 full-auto 운영된다는 주장.

## 추적

- TASK-NNN check list 항목과 연결되는 문서:
  - `agents/lead_engineer/tasks/TASK-NNN-agent-loop-runner.md`
  - `agents/lead_engineer/tasks/TASK-NNN-ralph-prompt-scaffold.md`
  - `agents/lead_engineer/tasks/TASK-NNN-orchestrator-message-bus.md`
  - `agents/lead_engineer/tasks/TASK-NNN-orchestrator-safety-gate.md`
  - `agents/lead_engineer/tasks/TASK-NNN-handoff-protocol.md`
