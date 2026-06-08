# 보고·기획 형식 (BRIEF / PLAN)

작성일: 2026-05-21
최종 개정: 2026-05-22
Owner: Lead Engineer
참조: [MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md), [TASK-NNN](tasks/TASK-NNN-briefing-format.md), [MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md), [TASK-NNN](tasks/TASK-NNN-reporting-format-efficiency.md)

---

## 목적

Owner 또는 CEO가 적은 토큰으로 빠르게 판단하되, 단순 작업 보고를 넘어 다음 판단에 필요한 인사이트를 얻도록 in-conversation 보고·기획 응답을 표준화한다.

저장 파일(MEETING/TASK/REVIEW/RETRO)의 형식이 아니라, 그 결과를 사용자에게 전달할 때 쓰는 출력 형식이다.

---

## 보고 2-layer (Executive / Technical) — 필수 (CEO 결정 2026-05-27)

작성 근거: [MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md) 결정 #2, [TASK-NNN](tasks/TASK-NNN-two-layer-reporting.md). CEO 는 경영·운영 담당이라 파일명·함수명·토큰 수 중심 보고를 이해하기 어렵다. 모든 CEO-facing BRIEF/PLAN 은 **2개 층**으로 쓴다.

### Executive Layer (먼저, 필수)

CEO 가 한눈에 이해하는 층. 목표는 **읽는 사람이 이해에 쓰는 집중력·시간을 최소화**하는 것이다.

- `Bottom Line:` 한 줄 — 무엇을 했고/제안하는가.
- **기술 용어·파일명·함수명·토큰 수를 쓰지 않는다.** 전문 용어가 꼭 필요하면 그 자리에서 한 구절로 풀어쓴다.
- **명료한 직설 서술**로. 문장을 짧게, 한 문단에 한 가지. **비유는 강제가 아니다** — 직설로 더 명확하면 비유하지 않는다. 비유는 직설로 설명이 안 될 때만 보조로.
- 상태는 신호등(정상/주의/위험)으로. 숫자는 의사결정에 필요한 것만 (비용·일정·리스크).
- "이게 왜 중요한가 / CEO 가 결정할 것은 무엇인가" 를 명시.

### Insight (전략 통찰, Executive 에 필수)

비용·시간 숫자 나열이 아니다. **우리 계획과 작업에 대한 분석**이다. 매 보고에 다음 중 해당하는 것을 담는다.

- **지금 단계** — 전체 그림에서 어디까지 왔는가.
- **활용 범위** — 지금까지 만든 것으로 무엇을 할 수 있는가 / 어디까지 쓸 수 있는가.
- **다음 가능성** — 이걸 토대로 다음에 무엇을 할 수 있는가.
- **효과** — 이 작업이 실제로 어떤 변화·이득을 주는가.

단순 "토큰 N · 시간 N" 은 Technical Layer 로 내린다. Executive 의 Insight 는 "그래서 우리가 어디로 가고 무엇이 가능해지는가" 다.

### Technical Layer (뒤에, 접어두기)

기술 상세 — 감사·재현·디버깅용. Executive 아래에 `<details>` 또는 명확한 `## Technical` 구분선 뒤에 둔다.

- 파일·함수·변경 라인·토큰·검증 명령·테스트 수·PR 번호·AUDIT ID.
- 기존 BRIEF/PLAN 형식(Signal 표·Insight·Decision·G/Y/R) 그대로.

### 작성 규칙

1. **Executive 가 먼저, 항상.** CEO 가 Technical 을 안 읽어도 의사결정 가능해야 한다.
2. Technical 은 생략 가능 (Mini BRIEF 등 단순 보고). 단 Critical/대형 작업은 둘 다.
3. 같은 사실을 두 번 쓰되 *번역* 한다 — Executive 는 "사람이 검토하는 다른 직원을 불러 교차 점검했다", Technical 은 "reviewer subagent VERDICT APPROVED, 80K tokens".
4. 진행 중 1~2문장 업데이트는 본 규칙 예외 (자유형).

---

## 대상별 통신 분리

핵심 원칙: **Owner/CEO와의 의사소통은 의사결정 최적화, 에이전트 간 소통은 기계 처리·감사 최적화**로 분리한다.

| 대상 | 목적 | 형식 | 금지/주의 |
|------|------|------|-----------|
| Owner / CEO | 판단, 승인, 우선순위, 리스크 이해 | BRIEF / PLAN, 표, 숫자, `Insight`, `Decision` | 내부 로그 원문 나열 금지. routine은 CEO, 에스컬레이션만 Owner |
| 에이전트 간 | 인수인계, claim, 상태 전이, 증거 링크 | TASK/MEETING/AUDIT/messages frontmatter, ID, status, owner, evidence | 설득형 문장·장식 금지 |
| 영구 기록 | 감사, 재현, 검색, 검증 | source of truth 문서(CYCLE/TASK/AUDIT/RETRO) | 대화체 요약만 남기기 금지 |

운영 규칙:
- Owner/CEO에게는 agent 내부 상태를 그대로 던지지 않고, 의사결정 가능한 `Bottom Line -> Signal -> Insight -> Decision`으로 변환한다.
- 에이전트 간 메시지는 짧고 구조화한다. `task_id`, `from`, `to`, `request`, `status`, `evidence`, `next`가 핵심이다.
- 같은 사건도 두 산출물로 나눌 수 있다. canonical 기록은 TASK/AUDIT에 남기고, 사용자 보고는 BRIEF로 압축한다.
- agent-facing 기록에는 감정적 표현, 과잉 배경 설명, 이모지를 쓰지 않는다. 검색성과 파싱 가능성을 우선한다.

---

## 설계 원칙

| 원칙 | 규칙 |
|------|------|
| 결론 우선 | 첫 줄에 `Bottom Line`을 둔다. |
| 토큰 절약 | 기본은 5~12줄. Full은 사용자가 요청하거나 결정이 많을 때만 쓴다. |
| 숫자 우선 | 형용사보다 `N건`, `%`, `ph`, `tokens`, `G/Y/R` 상태를 우선한다. |
| 인사이트 포함 | 모든 Standard 이상 보고에는 `Insight` 1~3개를 둔다. |
| 장식 최소 | 장식용 이모지는 쓰지 않는다. O/X/체크박스 같은 단순 상태 마커는 허용 (남발 금지). §이모지 정책 참조. |
| 시각 표현 | 표, ASCII bar, G/Y/R, delta(`+/-`)를 쓴다. |
| 직관성 | 열거 라벨은 숫자(`1. 2. 3.`)로 통일, 항목 사이 한 줄 띄움, 평가 4요소(우선순위·중요도·시간·토큰) 표기. §항목·옵션 제시 규칙 참조. |

---

## 상태 색상

마크다운/터미널 호환을 위해 실제 색상 대신 텍스트 라벨을 쓴다. UI로 옮길 때만 색으로 렌더링한다.

| 라벨 | 색상 의미 | 판단 기준 |
|------|-----------|-----------|
| `G` | Green | 정상, 결정 불필요, 계획 대비 허용 범위 |
| `Y` | Yellow | 주의, 결정 또는 후속 확인 필요 |
| `R` | Red | 차단, 비용/품질/보안 위험 |
| `B` | Blue | 정보, 추세나 참고 데이터 |

간단한 bar:

```text
진행  [####----] 50%
부담  [######--] 75% Y
리스크 [##------] 25% G
```

---

## 스케일 규칙

| 크기 | 기준 | 목표 길이 |
|------|------|-----------|
| Mini | 단순 완료/상태, 결정 0건 | 2~5줄 |
| Standard | TASK/작은 계획 1건, 결정 1~3건 | 8~15줄 |
| Full | 사이클/여러 TASK/대안 비교/리스크 큼 | 20~35줄 |

Full을 쓰는 조건:
- 사용자가 "전체", "종합", "비교", "기획서", "보고서"를 요청
- 결정 항목이 4건 이상
- 비용/보안/운영 리스크가 `R`
- 여러 역할 또는 여러 TASK의 trade-off가 핵심

---

## BRIEF (작업 보고)

### Mini BRIEF

```text
Bottom Line: <결과 한 줄>. 상태 <G/Y/R>, 결정 <없음/1건>.
Metric: <핵심 수치 1~2개>.
Next: <다음 행동 또는 없음>.
```

### Standard BRIEF

```text
Bottom Line: <결론 + 가장 중요한 의미>.

| Signal | Value |
|--------|-------|
| Status | G/Y/R |
| Scope | <완료/변경 범위> |
| Cost | <실측 vs 추정> |
| Verify | <검증 결과> |

Insight:
1. <작업 과정에서 드러난 패턴/병목/리스크>
2. <다음 계획에 반영할 보정값 또는 판단>

Decision:
1. <질문> -> 권장: <A>. 근거: <숫자/리스크>. 대안: <B>, 기각: <사유>.
```

### Full BRIEF

```text
Bottom Line: <1~2줄 결론>.

Scoreboard:
| Metric | Plan | Actual | Delta | State |
|--------|------|--------|-------|-------|

Work Map:
| Area | Done | Evidence | Risk |
|------|------|----------|------|

Insight:
1. Pattern: <반복 패턴>
2. Bottleneck: <병목>
3. Forecast: <다음 사이클 영향>

Decision:
1. <질문> -> 권장 / 근거 / 대안 / 기각 사유

Next:
| Step | Owner | Trigger |
|------|-------|---------|
```

---

## PLAN (기획)

### Mini PLAN

```text
Bottom Line: <목표 + 권장 접근>.
Scope: in <X> / out <Y>.
Cost: <N ph / ~N tokens>, risk <G/Y/R>.
Decision: <시작 전 필요한 결정 또는 없음>.
```

### Standard PLAN

```text
Bottom Line: <무엇을 왜 할지>.

| Field | Plan |
|-------|------|
| Goal | <측정 가능한 목표> |
| Scope | in <X> / out <Y> |
| Cost | <시간/token + 오차 범위> |
| Owner | <단일 Owner + 협업자> |
| Verify | <검증 명령/증거> |

Insight Target:
1. <이 작업을 통해 확인할 가정>
2. <다음 결정을 바꿀 수 있는 관찰값>

Decision:
1. <질문> -> 권장: <A>. 대안: <B>, 기각: <사유>.
```

### Full PLAN

```text
Bottom Line: <권장 계획 + 핵심 trade-off>.

Scoreboard:
| Option | Cost | Benefit | Risk | State |
|--------|------|---------|------|-------|

Phases:
| Phase | Work | Owner | Exit |
|-------|------|-------|------|

Insight Target:
1. <검증할 가정>
2. <얻어야 할 운영/제품/비용 인사이트>
3. <다음 계획에 반영할 지표>

Decision:
1. <질문> -> 권장 / 근거 / 대안 / 기각 사유 / 영향

DoD:
- <완료 기준>
- <검증 기준>
```

---

## 인사이트 작성 규칙

`Insight`는 "무엇을 했다"가 아니라 "그래서 다음 판단이 어떻게 바뀌는가"를 적는다.

| 유형 | 질문 | 예 |
|------|------|----|
| Pattern | 반복되는 현상은? | 추정 토큰이 계속 20~30% 과대다. |
| Bottleneck | 어디가 막히는가? | Lead Engineer 문서 작업이 owner 병목이다. |
| Leverage | 작은 수정으로 큰 효과가 나는 지점은? | `check_agent_docs.py` 경고를 줄이면 Review 비용이 감소한다. |
| Surprise | 예상과 달랐던 점은? | 이모지보다 표/숫자가 더 낮은 토큰으로 같은 신호를 준다. |
| Forecast | 다음 작업에 미치는 영향은? | CYCLE-NNN 추정치에 -15% 보정을 적용할 수 있다. |

규칙:
- Standard: Insight 1~2개
- Full: Insight 2~4개
- 각 Insight는 한 줄, 가능하면 숫자 1개 포함
- 근거 없는 추정은 `추정`이라고 표시

---

## 결정 항목 규칙

모든 `Decision`은 4요소를 가진다.

```text
<질문> -> 권장: <A>. 근거: <숫자/리스크>. 대안: <B>, 기각: <사유>.
```

결정이 없으면 `Decision: 없음`이라고만 쓴다.

선택지를 1·2·3 으로 제시할 때는 §항목·옵션 제시 규칙 의 정렬·표기·escape 규칙을 따른다.

---

## 항목·옵션 제시 규칙 (2026-05-24 강제)

작업 항목·선택지·다음 후보를 **둘 이상 나열**할 때 적용한다 (BRIEF/PLAN/대화 공통).

### 평가 4요소 (필수 표기)

각 항목에 다음 4요소를 함께 보고한다. 누락 금지.

| 요소 | 표기 | 의미 |
|------|------|------|
| 우선순위 | Critical / High / Medium / Low | 실행 순서 결정값 (= 기본 정렬 키) |
| 중요도 | 한 줄 근거 (가치·영향) | 왜 중요한가 (우선순위의 근거) |
| 시간 | `N ph` | 추정 소요 (person-hours) |
| 토큰 | `~N K` | 추정 LLM 토큰 |

### 정렬 (기본 키 = 우선순위)

기본 정렬은 **우선순위 내림차순** (Critical → High → Medium → Low). 동순위는 **가치/비용 비**(중요도 ÷ (시간·토큰))가 큰 순. 표/목록 위에 정렬 키를 한 줄로 명시한다.

근거(리서치, 2026-05-24): RICE·WSJF 등 표준 우선순위 프레임워크는 단일 점수(가치 ÷ 비용, +시간 민감도)로 순위를 매긴다. 즉 actionable 한 단일 정렬값은 raw 중요도나 비용 단독이 아니라 이들을 합성한 **우선순위**다. 중요도=분자, 시간·토큰=분모, 우선순위=합성 결과이므로 기본 정렬 키로 우선순위를 쓴다.

### 가시성·표기 통일

- **열거 라벨은 숫자 하나로 통일**: `1.` `2.` `3.`. 알파벳(a/b/c)·기호·대소문자 혼용 금지.
- canonical ID 가 있으면(예: `TASK-NNN`, todo `#6`) 본문에서 그 ID 로 지칭하되, 열거 자체는 숫자로 한다. ID 와 임의 라벨을 섞지 않는다.
- **항목 사이 한 줄 띄움**(빈 줄). 붙여 쓰지 않는다.
- 단위 표기 고정: 시간 `ph`, 토큰 `~K`, 상태 `G/Y/R/B`, 증감 `+/-%`.
- 한 항목 = 가능하면 한 줄(또는 표 한 행).

예시:

```text
정렬: 우선순위 내림차순

| # | 항목 | 우선순위 | 중요도 | 시간 | 토큰 |
|---|------|----------|--------|------|------|
| 1 | SDK live 검증 (#7) | High | TASK-NNN 안전망·결정적 경로 | 0.5 ph | ~8K |
| 2 | TASK-NNN 조사 (#6) | Medium | CLI 신뢰도 규명 | 2 ph | ~20K |
```

### 선택지 escape (결정 요청 시 필수)

사용자에게 1·2·3 형태로 결정을 물을 때(AskUserQuestion 포함), **마지막 2~3 선택지는 항상 escape 경로**로 둔다. 내가 제안한 안에만 갇히지 않게 한다.

```text
1. (내 권장안)
2. (대안)
…
N-2. 직접 입력 / 다른 방식 제안 (자유 요청)
N-1. 보류 / 나중에 결정
N. 거부 / 아무것도 안 함
```

AskUserQuestion 은 옵션 최대 4개 + 자동 "Other"(자유 입력)를 제공한다. 따라서 실제안 1~2개 + 명시적 escape(예: "보류/나중에", "거부") 를 넣고, 자유 채팅은 자동 Other 로 커버한다.

---

## 이모지 정책

기본값: **장식용 이모지는 쓰지 않는다. 단, 간단·직관적 상태 마커는 허용한다** (CEO 결정 2026-05-27).

허용 상태 마커 (간단·직관적, "남발" 금지):
- 체크/완료: `✓` `✔` `✅`
- 실패/취소: `✗` `✘` `❌`
- 체크박스: `☑` `☐` `☒`
- 동그라미(O): `⭕`
- 텍스트 라벨(`[완료]`, `G/Y/R`, `done`)도 그대로 유효 — 상황에 맞게 택일.

금지 (장식용):
- 얼굴/손/감정 이모지(🙂 👍 🎉 🔥 등), 트로피·로켓 등 데코레이션 픽토그램.
- 같은 상태 마커라도 **남발**(권장 한 문서 20개 이하). 초과 시 `check_agent_docs.py` 가 WARN.

이유:
- 상태 마커(O/X/체크)는 표·목록에서 빠른 스캔에 직접 기여 — 토큰 대비 신호 효율이 높다.
- 반면 장식 이모지는 에이전트별 해석 차이 + 시각 잡음만 늘린다.
- 따라서 "기능적 상태 표시는 허용, 장식·남발은 금지" 가 본 프로젝트 균형점.

강제: `scripts/check_agent_docs.py` 가 `reports/` 의 BRIEF/PLAN 을 스캔 — 허용 목록 밖 글리프는 ERROR, 허용 글리프 21개 이상은 WARN.

---

## 빠른 선택표

| 상황 | 형식 |
|------|------|
| "끝났어?" | Mini BRIEF |
| TASK 1건 완료 | Standard BRIEF |
| CYCLE 종료 | Full BRIEF |
| "이거 어떻게 할까?" | Standard PLAN |
| 새 사이클/여러 대안 | Full PLAN |
| 진행 중 업데이트 | 자유형 1~2문장, 형식 강제 없음 |

---

## 자동 보관 (reports archive)

BRIEF/PLAN 응답은 in-conversation 출력으로 끝나지 않고 [agents/lead_engineer/reports/](reports/) 디렉토리에 누적 저장한다 ([TASK-NNN](tasks/TASK-NNN-reporting-archive-harness.md), [MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md)).

| 항목 | 값 |
|------|-----|
| 저장 위치 | `agents/lead_engineer/reports/` |
| 파일명 | `BRIEF-YYYY-MM-DD-NNN.md` 또는 `PLAN-YYYY-MM-DD-NNN.md` |
| 색인 | [reports/INDEX.md](reports/INDEX.md) (시간 역순) |
| 스키마 | [reports/README.md](reports/README.md) §Frontmatter 스키마 |
| 헬퍼 CLI | `python scripts/save_report.py {brief\|plan} --title ... --audience ... --scale ... --body-file ... [--related-task ...]` |
| 강제력 | (1) 본 규칙 명문화 (2) `python scripts/check_agent_docs.py` reports 무결성·INDEX 정합 검출 |

규칙은 [AGENTS.md §10.5](../../AGENTS.md) "자동 보관" 절과 동일. 본 형식의 모든 BRIEF/PLAN 출력 (Mini/Standard/Full) 이 적용 대상이다. 자유형 1~2문장 진행 업데이트는 대상이 아니다.

---

## 변경 이력

- 2026-05-21 — 초안 작성 ([TASK-NNN](tasks/TASK-NNN-briefing-format.md)). BRIEF/PLAN 3크기, 이모지 어휘, TL;DR, 결정 항목 규칙 도입.
- 2026-05-21 — 효율 재설계 ([TASK-NNN](tasks/TASK-NNN-reporting-format-efficiency.md)). 이모지 기본 제거, 표/숫자/G-Y-R/ASCII bar 중심, `Insight` 섹션 필수화, 문서 길이와 샘플 과잉 축소.
- 2026-05-21 — 대상별 통신 분리 원칙 추가. CEO-facing은 인간 의사결정 최적화, agent-facing은 구조화·파싱·감사 최적화로 분리.
- 2026-05-27 — Owner/CEO 분리 반영 (TASK-NNN). routine 보고·명령 판단은 CEO, 파괴적/고위험 에스컬레이션만 Owner로 라우팅.
- 2026-05-22 — 자동 보관 절 추가 ([TASK-NNN](tasks/TASK-NNN-reporting-archive-harness.md)). BRIEF/PLAN 응답은 `agents/lead_engineer/reports/`에 누적 저장 + INDEX 갱신, `check_agent_docs.py` 가 무결성 검증.
- 2026-05-27 — 이모지 정책 완화 (CEO 결정, AUDIT-YYYY-MM-DD-NNN). 장식 이모지 금지는 유지하되 O/X/체크박스 계열 단순 상태 마커(`✓✔✅✗✘❌☑☐☒⭕`)는 허용. `check_agent_docs.py` 가 허용 목록 + 남발(>20) WARN 으로 강제.
