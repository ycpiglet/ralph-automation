"""
TASK frontmatter에서 파생 뷰를 생성한다.

사용:
  python scripts/generate_views.py

출력 파일 (모두 자동 생성, 직접 수정 금지):
  agents/lead_engineer/tasks/VIEW-by-owner.md
  agents/lead_engineer/tasks/VIEW-by-priority.md
  agents/lead_engineer/tasks/VIEW-by-status.md
  agents/lead_engineer/tasks/VIEW-by-tag.md

각 파일 최상단에 'Generated' 주석이 들어가며, frontmatter가 없는 legacy TASK는 제외한다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"


def parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        end = text.find("\n---", 4)
        if end == -1:
            return None
    body = text[4:end]
    result: dict = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
                result[key] = items
        elif (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result


def shell_timestamp() -> str:
    """셸에서 시각을 받아온다. LLM 추정 금지 규칙을 코드에서도 따른다."""
    try:
        out = subprocess.run(
            [sys.executable, "scripts/now.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_tasks() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for path in sorted(TASKS_DIR.glob("TASK-*.md")):
        if path.name.endswith("-result.md"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm is None:
            continue
        out.append((path, fm))
    return out


PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"]
STATUS_ORDER = ["진행 중", "대기", "보류", "완료"]


def header(title: str, generated_at: str) -> str:
    return (
        f"# {title}\n\n"
        f"> 이 파일은 `scripts/generate_views.py` 가 자동 생성한다. 직접 수정하지 말 것.\n"
        f"> 생성 시각: `{generated_at}`\n"
        f"> 원본: `agents/lead_engineer/tasks/TASK-*.md` 의 YAML frontmatter\n\n"
        f"필터링은 `python scripts/query_tasks.py --help` 참조.\n\n"
        "---\n\n"
    )


def task_row(fm: dict, path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    tags = ",".join(fm.get("tags") or [])
    return (
        f"| [{fm.get('id', '-')}]({rel.replace('agents/lead_engineer/tasks/', '')}) "
        f"| {fm.get('status', '-')} "
        f"| {fm.get('priority', '-')} "
        f"| {fm.get('difficulty', '-')} "
        f"| {fm.get('est_hours', '-')} ph / ~{fm.get('est_tokens', '-')} tok "
        f"| {fm.get('owner', '-')} "
        f"| {tags} |"
    )


def render_table(rows: list[tuple[Path, dict]]) -> str:
    if not rows:
        return "(no entries)\n"
    out = ["| ID | 상태 | 우선순위 | 난이도 | 예상 비용 | Owner | Tags |",
           "|----|------|----------|--------|-----------|-------|------|"]
    for path, fm in rows:
        out.append(task_row(fm, path))
    return "\n".join(out) + "\n"


def view_by_owner(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for path, fm in rows:
        groups[fm.get("owner") or "(unassigned)"].append((path, fm))
    out = [header("VIEW — TASK by Owner", generated_at)]
    for owner in sorted(groups):
        out.append(f"## {owner}\n\n{render_table(groups[owner])}\n")
    return "".join(out)


def view_by_priority(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for path, fm in rows:
        groups[fm.get("priority") or "(none)"].append((path, fm))
    out = [header("VIEW — TASK by Priority", generated_at)]
    seen = set()
    for p in PRIORITY_ORDER:
        if p in groups:
            out.append(f"## {p}\n\n{render_table(groups[p])}\n")
            seen.add(p)
    for p in sorted(set(groups) - seen):
        out.append(f"## {p}\n\n{render_table(groups[p])}\n")
    return "".join(out)


def view_by_status(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for path, fm in rows:
        groups[fm.get("status") or "(none)"].append((path, fm))
    out = [header("VIEW — TASK by Status", generated_at)]
    seen = set()
    for s in STATUS_ORDER:
        if s in groups:
            out.append(f"## {s}\n\n{render_table(groups[s])}\n")
            seen.add(s)
    for s in sorted(set(groups) - seen):
        out.append(f"## {s}\n\n{render_table(groups[s])}\n")
    return "".join(out)


def view_by_tag(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for path, fm in rows:
        for tag in fm.get("tags") or []:
            groups[tag].append((path, fm))
    out = [header("VIEW — TASK by Tag", generated_at)]
    for tag in sorted(groups):
        out.append(f"## #{tag}\n\n{render_table(groups[tag])}\n")
    return "".join(out)


OPEN_STATUSES = ["진행 중", "대기", "보류"]
LANE_ORDER = ["ACT", "REVIEW", "ASK", "DEFER"]
PRIORITY_WEIGHT = {"Critical": 40, "High": 30, "Medium": 20, "Low": 10}


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def task_title(path: Path) -> str:
    """Filename slug를 사람이 읽는 짧은 제목으로 바꾼다."""
    title = re.sub(r"^TASK-\d+-", "", path.stem).replace("-", " ")
    replacements = {
        "task176": "TASK-176",
        "crud": "CRUD",
        "e2e": "E2E",
        "qa": "QA",
        "uiux": "UI/UX",
        "ralph": "Ralph",
        "claude": "Claude",
    }
    words = []
    for word in title.split():
        words.append(replacements.get(word.lower(), word))
    return " ".join(words)


def gate_text(fm: dict) -> str:
    return (fm.get("gate") or "").strip()


def decision_lane(fm: dict) -> str:
    """Owner 스타일에 맞춘 실행 가능성 분류.

    ACT    = 지금 자율 실행 가능(R1/R2)
    REVIEW = 일부 자율 가능하지만 R3/Owner 경계가 있어 마무리 전 검토 필요
    ASK    = 외부/Owner 승인 없이는 진행 불가
    DEFER  = 의도적으로 미루는 것이 안전
    """
    status = fm.get("status", "")
    gate = gate_text(fm).lower()
    tags = [t.lower() for t in (fm.get("tags") or [])]
    if "deferred" in tags or "미빌드" in gate or "착수 판단" in gate or "불필요" in gate:
        return "DEFER"
    if status == "보류":
        return "ASK"
    if "r3" in gate or "owner" in gate or "승인" in gate or "등록" in gate or "확인" in gate:
        return "REVIEW"
    return "ACT"


def lane_label(lane: str) -> str:
    return {
        "ACT": "ACT — 자율 진행",
        "REVIEW": "REVIEW — 자율 가능 + 경계 확인",
        "ASK": "ASK — Owner/외부 게이트",
        "DEFER": "DEFER — 보류/낮은 지금 가치",
    }.get(lane, lane)


def effort_label(hours: float) -> str:
    if hours <= 1:
        return "XS"
    if hours <= 2:
        return "S"
    if hours <= 4:
        return "M"
    if hours <= 8:
        return "L"
    return "XL"


def impact_label(fm: dict) -> str:
    tags = set(fm.get("tags") or [])
    priority = fm.get("priority", "-")
    if "feedback" in tags or "frontend" in tags or "dashboard" in tags:
        return "사용자 가시 가치"
    if "scheduler" in tags or "automation" in tags or "routine" in tags:
        return "운영 자동화"
    if "qa" in tags or "e2e" in tags or "regression" in tags:
        return "회귀 리스크 감소"
    if "provider" in tags or "live" in tags:
        return "외부 게이트 해소"
    if "budget" in tags or "ledger" in tags:
        return "운영 안정성"
    return {"Critical": "치명 리스크", "High": "높은 가치", "Medium": "중간 가치", "Low": "낮은/위생"}.get(priority, "분류 필요")


def decision_score(fm: dict) -> int:
    """빠른 정렬용 휴리스틱. 절대 점수가 아니라 줄세우기 기준이다."""
    lane = decision_lane(fm)
    hours = num(fm.get("est_hours"))
    status_bonus = {"대기": 4, "진행 중": 2, "보류": -8}.get(fm.get("status", ""), 0)
    lane_bonus = {"ACT": 8, "REVIEW": 2, "ASK": -5, "DEFER": -12}.get(lane, 0)
    effort_penalty = min(12, int(hours))
    return PRIORITY_WEIGHT.get(fm.get("priority", ""), 0) + status_bonus + lane_bonus - effort_penalty


def decision_sort_key(row: tuple[Path, dict]) -> tuple:
    path, fm = row
    return (
        LANE_ORDER.index(decision_lane(fm)) if decision_lane(fm) in LANE_ORDER else 99,
        -decision_score(fm),
        num(fm.get("est_hours")),
        path.name,
    )


def short_gate(fm: dict) -> str:
    gate = gate_text(fm)
    if not gate and fm.get("status") == "보류":
        return "보류 상태 — 해제 조건은 TASK 본문 확인"
    if not gate:
        return "없음"
    return gate.replace("\n", " ")[:72] + ("..." if len(gate) > 72 else "")


def next_action(fm: dict) -> str:
    lane = decision_lane(fm)
    status = fm.get("status")
    if lane == "ACT" and status == "대기":
        return "바로 착수 후보"
    if lane == "ACT":
        return "마무리/진행 유지"
    if lane == "REVIEW":
        return "R2 범위 진행, R3 전 확인"
    if lane == "ASK":
        return "Owner/외부 조건 대기"
    return "지금은 보류"


def render_decision_table(rows: list[tuple[Path, dict]]) -> str:
    if not rows:
        return "(없음)\n"
    out = [
        "| Rank | Task | 결정 | 상태 | 중요도 | 시간 | 가치/이유 | 다음 행동 |",
        "|------|------|------|------|--------|------|-----------|-----------|",
    ]
    for idx, (path, fm) in enumerate(sorted(rows, key=decision_sort_key), 1):
        rel = path.relative_to(ROOT).as_posix().replace("agents/lead_engineer/tasks/", "")
        hours = num(fm.get("est_hours"))
        task = f"[{fm.get('id')}]({rel}) {task_title(path)}"
        priority = f"{fm.get('priority', '-')} / score {decision_score(fm)}"
        effort = f"{effort_label(hours)} · {hours:g} ph"
        why = impact_label(fm)
        if decision_lane(fm) in {"ASK", "REVIEW", "DEFER"}:
            why = f"{why}; gate: {short_gate(fm)}"
        out.append(
            f"| {idx} | {task} | {decision_lane(fm)} | {fm.get('status', '-')} "
            f"| {priority} | {effort} | {why} | {next_action(fm)} |"
        )
    return "\n".join(out) + "\n"


def metric_line(label: str, value: str, note: str) -> str:
    return f"| {label} | {value} | {note} |\n"


def render_snapshot(open_rows: list[tuple[Path, dict]]) -> str:
    active = [r for r in open_rows if r[1].get("status") == "진행 중"]
    waiting = [r for r in open_rows if r[1].get("status") == "대기"]
    blocked = [r for r in open_rows if r[1].get("status") == "보류"]
    total_hours = sum(num(fm.get("est_hours")) for _, fm in open_rows)
    lane_counts = {lane: 0 for lane in LANE_ORDER}
    for _, fm in open_rows:
        lane_counts[decision_lane(fm)] = lane_counts.get(decision_lane(fm), 0) + 1
    wip_note = "WIP 과다 — 새 착수보다 마무리/게이트 해소 우선" if len(active) > 3 else "WIP 적정"
    act_waiting = [r for r in waiting if decision_lane(r[1]) == "ACT"]
    top = sorted(act_waiting, key=decision_sort_key)[0] if act_waiting else None
    top_note = f"{top[1].get('id')} ({task_title(top[0])})" if top else "대기 중 ACT 후보 없음"

    out = [
        "## 한눈에 보기\n\n",
        "| 지표 | 값 | 해석 |\n",
        "|------|----|------|\n",
        metric_line("열린 작업", f"{len(open_rows)}건 / {total_hours:g} ph", "frontmatter 기준"),
        metric_line("진행 중 WIP", f"{len(active)}건", wip_note),
        metric_line("대기", f"{len(waiting)}건", f"최상위 자율 후보: {top_note}"),
        metric_line("보류", f"{len(blocked)}건", "Owner/외부 조건 또는 의도적 defer"),
        metric_line("실행성", " / ".join(f"{k} {v}" for k, v in lane_counts.items()), "ACT는 승인 없이 진행 가능, ASK/DEFER는 멈춤"),
        "\n",
        "**빠른 판단:** ",
    ]
    if top:
        out.append(f"새로 하나를 고른다면 **{top[1].get('id')}**. 다만 현재 WIP가 {len(active)}건이라, 먼저 진행 중 항목을 줄이는 편이 흐름에 유리하다.\n\n")
    else:
        out.append("새 착수보다 진행 중/게이트 항목 정리가 우선이다.\n\n")
    return "".join(out)


def backlog_row(fm: dict, path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix().replace("agents/lead_engineer/tasks/", "")
    # 보류는 게이트(해제 조건)를, 나머지는 태그를 비고로 — 둘 다 frontmatter 에서만(드리프트 불가)
    note = fm.get("gate") or ",".join(fm.get("tags") or []) or "—"
    return (
        f"| [{fm.get('id', '-')}]({rel}) "
        f"| {fm.get('priority', '-')} "
        f"| {fm.get('owner', '-')} "
        f"| {fm.get('est_hours', '-')} ph / ~{fm.get('est_tokens', '-')} tok "
        f"| {note} |"
    )


def render_backlog_table(rows: list[tuple[Path, dict]]) -> str:
    if not rows:
        return "(없음)\n"
    pr = {p: i for i, p in enumerate(PRIORITY_ORDER)}
    rows = sorted(rows, key=lambda r: pr.get(r[1].get("priority", ""), 9))
    out = ["| ID | 우선순위 | Owner | 예상 | 게이트 / 태그 |",
           "|----|----------|-------|------|----------------|"]
    for path, fm in rows:
        out.append(backlog_row(fm, path))
    return "\n".join(out) + "\n"


def view_backlog(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    """열린 작업(진행 중·대기·보류) 단일 canonical 포인터. 어느 환경이든 같은 최신 상태에 수렴."""
    groups: dict[str, list] = defaultdict(list)
    lanes: dict[str, list] = defaultdict(list)
    open_rows: list[tuple[Path, dict]] = []
    for path, fm in rows:
        st = fm.get("status")
        if st in OPEN_STATUSES:
            groups[st].append((path, fm))
            lanes[decision_lane(fm)].append((path, fm))
            open_rows.append((path, fm))
    n = len(open_rows)
    out = [
        "# BACKLOG — 의사결정 보드 (repo-canonical)\n\n",
        "> **이것이 \"지금 무엇이 열려 있고 다음에 무엇을 하나\"의 단일 출처다.**\n",
        "> 어느 세션/PC/OS/에이전트/사용자든 작업 시작 시 `git pull` 후 이 파일과\n",
        "> `python scripts/backlog_sweep.py`(due-check 등 런타임 신호 포함)를 본다.\n",
        "> `scripts/generate_views.py` 가 TASK frontmatter 에서 생성 → 드리프트 불가. **직접 수정 금지.**\n",
        "> **규칙(COMPOUND-032): 열린 작업은 전부 TASK 로 존재해야 한다** — 메모리·프로세 \"다음:\" 한 줄에만 두지 말 것\n",
        "> (로컬 메모리는 PC/사용자별이라 공유 불가 → 다른 세션이 못 봐서 중복작업이 생긴다).\n",
        f"> 생성 시각: `{generated_at}` · 열린 작업 {n}건\n\n",
        "---\n\n",
        "## 표시 원칙\n\n",
        "- `ACT`: 가역(R1/R2)이라 승인 없이 진행 가능.\n",
        "- `REVIEW`: 일부는 자율 가능하지만 R3/Owner 경계가 있어 마무리 전 확인 필요.\n",
        "- `ASK`: Owner 승인, 외부 계정/결제/secret 등 없이는 진행 불가.\n",
        "- `DEFER`: 지금은 일부러 미루는 것이 안전하거나 가치가 낮음.\n",
        "- `score`: 우선순위, 실행 가능성, 예상 시간을 섞은 정렬용 휴리스틱이다. 절대값이 아니라 줄세우기 기준이다.\n\n",
        render_snapshot(open_rows),
        "## 결정 레인\n\n",
    ]
    for lane in LANE_ORDER:
        if lanes.get(lane):
            out.append(f"### {lane_label(lane)}\n\n{render_decision_table(lanes[lane])}\n")

    out.append("## 흐름 보드\n\n")
    titles = {"진행 중": "### 진행 중 (active)", "대기": "### 대기 (next)", "보류": "### 보류 (게이트 — 외부/결정 대기)"}
    for st in OPEN_STATUSES:
        if groups.get(st):
            out.append(f"{titles[st]}\n\n{render_backlog_table(groups[st])}\n")
    return "".join(out)


def view_by_workload(rows: list[tuple[Path, dict]], generated_at: str) -> str:
    """owner/assignee별 누적 시간·토큰을 표로 정리한다 (scorecard 와 같은 데이터)."""
    def num(v):
        try: return float(v)
        except (TypeError, ValueError): return 0.0

    by_owner: dict[str, dict] = defaultdict(lambda: {"count": 0, "hours": 0.0, "tokens": 0.0, "ch": 0.0})
    by_assignee: dict[str, dict] = defaultdict(lambda: {"count": 0, "hours": 0.0, "tokens": 0.0})
    for _, fm in rows:
        owner = fm.get("owner") or "(unassigned)"
        h = num(fm.get("est_hours"))
        t = num(fm.get("est_tokens"))
        assignees = fm.get("assignees") or [owner]
        prio = fm.get("priority") or ""
        by_owner[owner]["count"] += 1
        by_owner[owner]["hours"] += h
        by_owner[owner]["tokens"] += t
        if prio in ("Critical", "High"):
            by_owner[owner]["ch"] += h
        for a in assignees:
            by_assignee[a]["count"] += 1
            by_assignee[a]["hours"] += h / max(1, len(assignees))
            by_assignee[a]["tokens"] += t / max(1, len(assignees))

    out = [header("VIEW — TASK by Workload (cost & concentration)", generated_at)]
    out.append("> 누적 추정 비용 기준. 실측 비용은 TASK 본문 완료 기록에서 별도 추적.\n")
    out.append("> 자세한 경고(Critical/High 집중, 실측 누락)는 `python scripts/agent_scorecard.py` 참조.\n\n")

    out.append("## By Owner\n\n")
    out.append("| Owner | TASKs | Hours | Tokens | Critical/High Hours |\n")
    out.append("|-------|-------|-------|--------|---------------------|\n")
    for owner in sorted(by_owner, key=lambda k: -by_owner[k]["hours"]):
        d = by_owner[owner]
        out.append(f"| {owner} | {d['count']} | {d['hours']:.1f} ph | ~{d['tokens'] / 1000:.0f}K | {d['ch']:.1f} ph |\n")

    out.append("\n## By Assignee (공동 작업 분할 반영)\n\n")
    out.append("| Assignee | TASKs | Hours (share) | Tokens (share) |\n")
    out.append("|----------|-------|---------------|----------------|\n")
    for a in sorted(by_assignee, key=lambda k: -by_assignee[k]["hours"]):
        d = by_assignee[a]
        out.append(f"| {a} | {d['count']} | {d['hours']:.1f} ph | ~{d['tokens'] / 1000:.0f}K |\n")

    return "".join(out)


def build_artifacts(ts: str) -> dict[str, str]:
    rows = load_tasks()
    return {
        "BACKLOG.md": view_backlog(rows, ts),
        "VIEW-by-owner.md": view_by_owner(rows, ts),
        "VIEW-by-priority.md": view_by_priority(rows, ts),
        "VIEW-by-status.md": view_by_status(rows, ts),
        "VIEW-by-tag.md": view_by_tag(rows, ts),
        "VIEW-by-workload.md": view_by_workload(rows, ts),
    }


def _strip_volatile(body: str) -> str:
    """Ignore timestamp lines so --check can compare generated content without rewriting."""
    return re.sub(r"^> 생성 시각:.*$", "> 생성 시각: <ignored>", body, flags=re.MULTILINE)


def check_views() -> int:
    ts = shell_timestamp()
    artifacts = build_artifacts(ts)
    stale: list[str] = []
    for name, fresh in artifacts.items():
        path = TASKS_DIR / name
        if not path.exists():
            stale.append(f"{name} (missing)")
            continue
        on_disk = path.read_text(encoding="utf-8")
        if _strip_volatile(on_disk) != _strip_volatile(fresh):
            stale.append(name)
    if stale:
        print("STALE: " + ", ".join(stale), file=sys.stderr)
        print("Run `python scripts/generate_views.py` to refresh.", file=sys.stderr)
        return 1

    try:
        import build_task_index
        bad = build_task_index.check_against_schema()
    except Exception as exc:
        print(f"ERROR: task index schema check failed to run: {exc}", file=sys.stderr)
        return 1
    if bad:
        for tid, errs in bad.items():
            for err in errs:
                print(f"ERROR {tid}: {err}", file=sys.stderr)
        return 1

    print(f"OK: TASK views up-to-date ({len(load_tasks())} tasks / {len(artifacts)} views)")
    return 0


def write_views() -> int:
    rows = load_tasks()
    outputs = build_artifacts(shell_timestamp())
    written = []
    for name, content in outputs.items():
        path = TASKS_DIR / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path.relative_to(ROOT)).replace("\\", "/"))

    print(f"Generated {len(written)} view(s) from {len(rows)} TASK(s) with frontmatter:")
    for w in written:
        print(f"  - {w}")

    # 구조화 task 인덱스(TASK-232)도 같이 갱신 — UI·에이전트 read 표면. gitignore(파생).
    try:
        import build_task_index
        idx = build_task_index.write_index()
        print(f"  - {idx.relative_to(ROOT)} (derived, gitignored)")
    except Exception as exc:
        print(f"  (task index 갱신 생략: {exc})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or check TASK BACKLOG/VIEW-*.md files.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check generated TASK views and task schema without writing files.",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check_views()
    return write_views()


if __name__ == "__main__":
    sys.exit(main())
