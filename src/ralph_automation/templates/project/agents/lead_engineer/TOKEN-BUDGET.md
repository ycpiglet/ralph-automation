# TOKEN-BUDGET.md — 작업별 토큰 비용 카탈로그

작성일: 2026-05-22
최종 측정 모델: Claude Opus 4.7 (1M context)
Owner: Lead Engineer
참조: [MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md), [TASK-NNN](tasks/TASK-NNN-session-budget-protocol.md)

---

## 목적

각 운영/구현 작업이 소비하는 LLM 토큰을 실측 기반으로 카탈로그화한다. **목표는 단 한 가지** — 사용자가 작업 시작 전에 "이 작업으로 토큰이 얼마나 들지" 를 추정해, 토큰 부족으로 인한 갑작스러운 중단을 방지한다.

본 카탈로그는 절대값이 아닌 **추정 ±50%**. 정확한 값은 사용자의 LLM provider UI에서 직접 확인.

---

## 측정 방법

1. **단위**: LLM 입출력 합산 토큰 (input prompt + output generation + tool call I/O).
2. **변환**: 평균 8 tokens/line (markdown + 코드 + 한글/영어 혼용). 영어 100%면 ~4 tokens/word. 한글 100%면 ~2-3 tokens/character.
3. **실측 출처**: 본 세션 (2026-05-22, Claude Opus 4.7) 7 PR 누적 ~5443 insertions + 회의록 2건 + BRIEF 6건 + PLAN 2건 + 도구 호출 결과.
4. **재측정**: 매 CYCLE Review 시 RETRO §5 Forward Actions 후보로 갱신.

---

## 카탈로그 — 운영/로깅 작업

| 작업 | 라인 수 | 추정 토큰 (출력만) | LLM 입출 합산 추정 | 빈도 |
|------|---------|---------------------|---------------------|------|
| TASK 정의 (frontmatter + 본문 5W1H + Context Snapshot + 체크리스트) | 170~240 | 1.5K | **3~5K** | TASK당 1회 |
| 회의록 — 분석/기획 유형 (의견/논거 5종 + 결정 사유 6+ + 도출 TASK 표) | 250~330 | 2.5K | **6~10K** | MEETING당 1회 |
| 회의록 — 의사결정/진행 점검 (의견·결정 사유 섹션 선택) | 80~150 | 1K | **2~4K** | MEETING당 1회 |
| BRIEF (Standard, Bottom Line + Signal 표 + Insight + Decision) | 35~60 | 0.4K | **1~2K** | TASK 완료당 1회 |
| BRIEF (Mini, 결과 한 줄) | 3~10 | 0.1K | **0.2~0.5K** | 가벼운 보고 |
| BRIEF (Full, Scoreboard + Work Map + Forecast) | 80~150 | 1K | **3~6K** | CYCLE 종료 |
| PLAN (Standard) | 40~60 | 0.5K | **1~2K** | 신규 작업 제안 |
| TASK 완료 기록 (Medium, 검증 + 변경 파일 + 리뷰 + 인수) | 60~120 | 0.8K | **2~4K** | TASK당 1회 |
| TASK 완료 기록 + `## Independent Audit` (High/Critical) | 100~180 | 1.5K | **4~8K** | High/Critical 완료 |
| **Handoff 섹션** (4단 구조 + 검증한 명령 + 다음 단계 + 주의) | 50~120 | 1K | **3~6K** | Handoff 트리거 시 |
| 운영 메타 5종 동시 갱신 (STATUS / tasks INDEX / assignment_log / AUDIT-LOG / meetings INDEX) | 60~100 | 0.7K | **2~4K** | TASK/MEETING당 1회 |
| AUDIT-LOG 단일 항목 추가 (시각/대상/작업/방법/결과/검증/리스크) | 15~25 | 0.2K | **0.5~1K** | 운영 변경 |
| Compound 항목 추가 (날짜/패턴/원인/개선/대상/상태) | 10~20 | 0.15K | **0.3~0.8K** | 반복 실수 발견 |
| Review 작성 (REVIEW-NNN.md, 완료/이월/회귀/검증/Compound 여부) | 80~120 | 1K | **3~5K** | CYCLE 종료당 1회 |
| RETRO 작성 (역할별, 5섹션 + Health Check + Forward Actions) | 100~180 | 1.5K | **4~8K** | CYCLE 종료당 역할당 1회 |
| Commit 메시지 + PR 본문 + 머지 | 60~120 | 0.8K | **2~3K** | PR당 1회 |
| `check_agent_docs.py` / `check_messages.py` / `generate_report_views.py --check` 실행 | 0 | 0 | **~0.3K** (출력 읽기만) | PR당 1회 |
| `python scripts/now.py` 호출 | 0 | 0 | **~0.05K** | 매 시각 캡처 |

**로깅 오버헤드 풀 사이클 (TASK 1건 완료)**:
- Medium: 회의록(선택) + TASK 정의 + 완료 기록 + BRIEF + 운영 메타 + AUDIT + commit/PR = **~12-20K**
- High: 추가로 Independent Audit + Handoff(트리거 시) = **~18-30K**
- Critical: 추가로 Review/Compound 가능성 = **~25-40K**

---

## 카탈로그 — 라이브 검증 호출 (subagent / council)

CYCLE-NNN/015 에서 일상화된 라이브 Agent tool subagent 호출은 코드 작성 토큰과 분리해 산정한다. dominant 비용 축이라 누락 시 추정이 +35% 빗나간다 ([COMPOUND-013](compound_log.md)).

| 호출 패턴 | 추정 토큰 (호출당) | 근거 |
|----------|---------------------|------|
| reviewer subagent 단일 호출 (diff-only, T0) | **~30-50K** | TASK-NNN dogfood 38K |
| reviewer subagent 단일 호출 (확장, T1+) | **~60-90K** | TASK-NNN reviewer ~80K |
| council 3-agent (implementer + reviewer + skeptic) | **~150-200K** | TASK-NNN 라이브 ~165K |
| Q&A 메시지 단일 (question + answer) | **~5-10K** | TASK-NNN 라이브 |
| seminar 블라인드 Delphi (TASK-NNN, N=3 round) | **~80-120K** | TASK-NNN 라이브 |

**추정 시 분리 산정 의무** (COMPOUND-013 적용 완료):

1. 시간(구현 노력) → "비용을 줄이는 패턴" 보정 비율 적용 (hotfix 10-15% / 운영 메타 25-40% / 신규 영역 50-70%)
2. 토큰(라이브 검증 호출) → 위 카탈로그 × 예상 호출 횟수
3. 합산 = 시간 보정 적용 토큰 + 라이브 호출 토큰

예: reviewer subagent 1회 포함 Medium TASK 추정 = 운영 메타 ~10K (시간 보정) + reviewer ~40K = ~50K.

§16 협업 등급별 권장 호출 횟수:

| 등급 | 권장 라이브 호출 | 토큰 가중 (추가) |
|------|-----------------|-----------------|
| Critical / High | council 1회 + reviewer 1회 (별 시각) | ~200K |
| Medium | reviewer 1회 | ~40-80K |
| Low | self-review만 (라이브 호출 없음) | ~0 |

## MCP 직접 호출 (CYCLE-NNN ENTRY-005 데이터)

| 호출 패턴 | 추정 토큰 (호출당 모음) | 근거 |
|----------|-------------------------|------|
| Playwright MCP 자동 탐색 (~10 tool calls / DOM evaluate + screenshot) | **~15-25K** | CYCLE-NNN ENTRY-005 ~20K |
| 기타 MCP 도구 (Managed database / Figma / Notion 등) 직접 호출 | **~5-30K** | 도구 응답 크기 따라 변동 |

**MCP 직접 호출은 subagent dispatch 와 다른 dimension** (AGENT-RUNTIME-LOG ENTRY-005 가설 입증):
- subagent ~90-150K = *codebase read + edit + 검증* dominant
- MCP ~15-25K = *외부 시스템 동적 시뮬레이션* (prod 사이트 / DB / 디자인 도구) — 즉시 반환
- 세 dimension (self / subagent / MCP) = *대체 아니라 작업 종류별 도구 선택*

caller-side accounting (MCP 외부 usage 메타 부재) — parent Claude 가 호출당 토큰 추적 시 sum.

---

## 카탈로그 — 코드 작업 (변동 큼)

| 작업 | 추정 토큰 (입출 합산) | 비고 |
|------|------------------------|------|
| 신규 Python 스크립트 + 검증 (예: query_reports.py) | **10~25K** | 코드 ~150-300줄 + dry-run 검증 |
| 기존 스크립트 확장 + 통합 (예: agent_orchestrator.py에 safety gate 통합) | **8~20K** | 함수 추가 + 통합 + 회귀 검증 |
| `check_agent_docs.py` 룰 1종 추가 (function ~30-80줄) | **5~12K** | negative test 포함 |
| AGENTS.md 새 절 신설 (예: §13 76줄) | **5~10K** | + 본문 cross-link |
| 부트스트랩 4종 동기화 (각 1-2줄 추가) | **2~4K** | 4종 일관성 |
| `agents/roles.yml` 신설 (9 role × 7 field) | **3~6K** | YAML + 문서 |
| 일반 코드 디버그 1건 | **2~8K** | 문제 크기 따라 |

**코드 작업 풀 사이클**:
- 소형 (CLI 1종 + 검증): **~15-25K**
- 중형 (모듈 + 통합 + 룰 추가 + 부트스트랩 동기): **~30-50K**
- 대형 (다중 파일 + 운영 규칙 변경 + 검증 다중): **~50-100K**

---

## 사이클 단위 추정

| 사이클 규모 | 토큰 예산 | 비고 |
|-------------|-----------|------|
| 단일 TASK (Medium) | ~30K | 로깅 + 코드 합산 |
| 단일 TASK (High) | ~50K | + Audit + Handoff trigger 가능성 |
| 단일 TASK (Critical) | ~70K | + Review/Compound 가능성 |
| **TASK 묶음 PR (2-3건)** | **~100-150K** | 본 세션 TASK-NNN+085 통합 PR(31 files, 1717 lines) ≈ ~80K |
| 한 세션 (Claude Opus 4.7, 1M context) 안전 작업량 | **~200-400K** | 1M의 20-40%. 나머지는 시스템/대화 누적 |

---

## Handoff Reserve (예약 토큰)

**§Handoff 섹션 작성 단독 비용 = 3-6K**.
**Reserve 권장 = 10K** (Handoff + 운영 메타 5종 갱신 + commit/PR 본문 합산, 여유 포함).

작업 도중 잔량이 reserve 이하로 떨어지면 **즉시 §Handoff 작성 + 운영 메타 동기 + commit**으로 전환. AGENTS.md §13.2 4단 구조 적용.

---

## 비용을 줄이는 패턴 (CLI 재사용 효과)

본 세션 (2026-05-22) 데이터 — 같은 CLI 패턴 (frontmatter 파서 + argparse subcommand + check 룰) 재사용 시:

| TASK | 추정 ph | 실측 ph | 절감률 | 비고 |
|------|---------|---------|--------|------|
| TASK-NNN (자동 보관 1차) | 4 | 1.5 | -62% | 신규 영역 |
| TASK-NNN (인덱싱) | 2 | 0.8 | -60% | 패턴 재사용 (query_tasks 차용) |
| TASK-NNN (CI VIEW stale) | 0.5 | 0.3 | -40% | 작은 룰 추가 |
| TASK-NNN (role registry) | 4 | 0.6 | -85% | YAML 단순 |
| TASK-NNN (Handoff Protocol) | 2 | 0.5 | -75% | 문서 위주 |
| TASK-NNN (UX 4건 정리) | 3 | 0.4 | -87% | sortSites 단일 정의처, 죽은 코드 제거 |
| TASK-NNN (agent_terminal 자동화) | 2.5 | 0.2 | -92% | hotfix-precursor 패턴 |
| TASK-NNN (wt `;` escape) | 0.5 | 0.05 | -90% | hotfix (1줄) |
| TASK-NNN (wt cwd + quote) | 0.5 | 0.05 | -90% | hotfix (2건) |
| TASK-NNN (PowerShell Set-Location) | 0.5 | 0.05 | -90% | hotfix |
| TASK-NNN (Agent Worker Loop) | 3 | 0.1 | **-97%** | demo agent 폴링 패턴 차용 |
| **평균** | | | **~-77%** | (이전 -65% → CYCLE-NNN 데이터 추가) |

**보정값** (CYCLE-NNN 재교정, COMPOUND-005 trigger):

| 작업 종류 | 추정 대비 실측 비율 | 적용 시점 |
|-----------|---------------------|-----------|
| **Hotfix 시리즈** (단일 줄/파일, 가시 검증 fast-feedback) | **~10-15%** | TASK-NNN/097/098 패턴 |
| **데모/패턴 직접 차용** (demo agent → production, 같은 폴링/파서) | **~10-20%** | TASK-NNN 패턴 |
| **운영 메타 재사용** (CLI subcommand, frontmatter 파서 재사용) | **~25-40%** | TASK-NNN/090/091/092 |
| **신규 영역** (새 모듈 + 새 검증 + 새 통합) | **~50-70%** | 첫 케이스 추정 시 사용 |
| **대형 신규** (다중 파일 + 운영 규칙 변경 + 검증 다중) | **~60-80%** | 안전 추정 |

추정 시 위 표에서 작업 종류를 먼저 분류 → 카탈로그 raw 추정 × 보정 비율 = 실측 추정.

---

## 절전 모드 운영 가이드 (잠정 — TASK-NNN 확정 전)

| 잔량 추정 (1M context 기준) | 권장 행동 |
|---------------------------|-----------|
| 100% ~ 60% | 자유 — 큰 TASK 가능 (~100K까지) |
| 60% ~ 30% | 중간 — Medium TASK 1건 시작 가능 (~30-50K) |
| 30% ~ 10% | 절약 — 새 TASK 시작 금지. 진행 중인 작업 마무리 + 자동 보관 |
| 10% ~ 0% | **위험 — 즉시 §Handoff 작성 + 머지 + 새 세션 권고** |

(사용자가 LLM provider UI에서 실제 잔량 확인 후 본 표 참조.)

---

## 변경 이력

- 2026-05-22 — 초안 작성 ([MEETING-YYYY-MM-DD-NNN](meetings/MEETING-YYYY-MM-DD-NNN.md)). 본 세션 7 PR 누적 데이터 기반. ±50% 정확도 명시.
- 2026-05-23 — CYCLE-NNN 재교정 (COMPOUND-005 trigger). TASK-NNN/095/096/097/098/099 실측 6건 추가. "비용을 줄이는 패턴" 평균이 -65% → -77% 로 강화. 보정값을 단일 "30-40%" 에서 **작업 종류별 5단계** (hotfix 10-15% / 데모 차용 10-20% / 운영 메타 재사용 25-40% / 신규 영역 50-70% / 대형 신규 60-80%) 로 세분화. 다음 추정부터 분류 → 보정 비율 적용 의무. AUDIT-YYYY-MM-DD-NNN.
- 2026-05-28 — TASK-NNN (CYCLE-NNN Wave 1) — 라이브 subagent / council 비용 분류 신설 (COMPOUND-013 적용 완료). reviewer T0 ~30-50K, T1+ ~60-90K, council 3-agent ~150-200K, Q&A ~5-10K, seminar ~80-120K. 추정 시 시간(구현)·토큰(라이브 호출) 분리 산정 의무. §16 등급별 권장 호출 횟수 표 추가. AUDIT-YYYY-MM-DD-NNN.
- 2026-05-28 — TASK-NNN (CYCLE-NNN) — MCP 직접 호출 비용 분류 신설 (CYCLE-NNN ENTRY-005 데이터). Playwright MCP ~15-25K / 기타 MCP ~5-30K. 세 dimension (self/subagent/MCP) 가설 명문화. COMPOUND-013 외부 검증 5번째 데이터 포인트. AUDIT-YYYY-MM-DD-NNN.
