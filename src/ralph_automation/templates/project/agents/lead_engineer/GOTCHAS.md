# Lead Engineer Gotchas

## Central Ledger Is Not Role Ownership

`agents/lead_engineer/tasks/`가 TASK canonical home이지만, 모든 작업을 Lead가 소유한다는 뜻은 아니다. QA/Beta/Doc/Backend/UI evidence는 해당 role directory에 두고 TASK에서 링크한다.

## High Diff Closure

`scripts/cycle_gate.py --diff origin/main`이 High 이상이면 reviewer/skeptic 또는 council뿐 아니라 required worker role evidence도 REVIEW/AUDIT에 남긴다. Subagent 호출은 self-review를 대체하지 않는다.

## WIP First

진행 중 작업이 많을 때는 새 High-value ACT를 바로 시작하지 않는다. dirty completion bundle, stale WIP, R3 잔여 분리를 먼저 닫는다.

## Generated Views

`BACKLOG.md`와 `VIEW-by-*.md`는 `scripts/generate_views.py` 산출물이다. TASK frontmatter/INDEX를 바꾼 뒤 재생성하고, 직접 손편집하지 않는다.
