from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TASK_STATES = {"대기", "진행 중", "완료", "보류"}
AUDIT_REQUIRED_TASK_NUMBER = 47
FRONTMATTER_REQUIRED_TASK_NUMBER = 48  # TASK-048+ must carry YAML frontmatter
AUDIT_TASK_FIELDS = ["요청 시각", "기록 시각", "요청자", "수행자", "의도", "대상", "방법", "감사 로그"]
AUDIT_COMPLETION_FIELDS = ["완료 시각", "검토자", "감사 로그"]
AUDIT_LOG_FIELDS = ["시각:", "요청자:", "수행자:", "의도:", "대상:", "작업:", "방법:", "결과:", "검증:", "관련 기록:", "남은 리스크:"]
ALLOWED_PRIORITY = {"Critical", "High", "Medium", "Low"}
ALLOWED_DIFFICULTY = {"낮", "중", "중-상", "상"}
ALLOWED_MEETING_TYPES = {"분석", "기획", "의사결정", "진행 점검"}
ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AUDIT_ID_RE = re.compile(r"^AUDIT-\d{4}-\d{2}-\d{2}-\d{3}$")
MEETING_ID_RE = re.compile(r"^MEETING-\d{4}-\d{2}-\d{2}-\d{3}$")
TASK_ID_RE = re.compile(r"^TASK-\d{3,}$")
SKIP_LINK_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "app://",
    "plugin://",
    "file://",
)


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML frontmatter parser. Returns dict or None if absent.

    Supports only scalar values and inline lists ([a, b, c]).
    Comments and multiline values are not supported — keep frontmatter simple.
    """
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
                items = []
                for item in inner.split(","):
                    item = item.strip().strip("'\"")
                    if item:
                        items.append(item)
                result[key] = items
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result


# TASK-236 (COMPOUND-034): 완료 High/Critical TASK 의 협업 증거 판정 — 모듈 레벨(테스트 가능).
# 실 협업(이름있는 외부 검토자/council/collab 이벤트) 또는 사유 동반 명시 waiver 만 통과.
# bare 'subagent' substring 루프홀은 통과시키지 않는다(soft WARN 회피의 핵심 구멍).
_REAL_COLLAB_RE = re.compile(
    r"(code[- ]?review|reviewer\s+subagent|skeptic|auditor\s+subagent|독립\s*검토|"
    r"council|consensus|collab[_-]?log|collab-\d{4}-\d{2}-\d{2}|multi_agent_v1|"
    r"subagent.{0,12}dispatch|dispatch.{0,12}subagent)", re.IGNORECASE)
_COLLAB_WAIVER_RE = re.compile(
    r"(협업\s*waiver|collab[- ]?waiver)\s*[:：(]?.{0,60}?"
    r"(budget|scope|예산|단일\s*세션|single[- ]?session|하네스|trivial|범위)",
    re.IGNORECASE | re.DOTALL)


def collab_evidence_present(body: str) -> bool:
    """완료 TASK 본문에 실 협업 증거 또는 사유 동반 명시 waiver 가 있으면 True."""
    return bool(_REAL_COLLAB_RE.search(body) or _COLLAB_WAIVER_RE.search(body))


_ROUTING_EVIDENCE_RE = re.compile(
    r"(routing[_-]?ref|eval[_-]?ref|eval_log\.jsonl|"
    r"model routing\s*[:：].{0,120}?(selected|policy|haiku|sonnet|opus)|"
    r"모델\s*라우팅\s*[:：].{0,120}?(selected|policy|haiku|sonnet|opus)|"
    r"selected[_-]?model\s*[:=]|policy[_-]?model\s*[:=]|policy[_-]?tier\s*[:=])",
    re.IGNORECASE,
)
_ROUTING_WAIVER_RE = re.compile(
    r"(routing\s*waiver|라우팅\s*waiver)\s*[:：(]?.{0,80}?"
    r"(main[- ]?session|메인\s*세션|scope|범위|unavailable|불가)",
    re.IGNORECASE | re.DOTALL,
)


def routing_evidence_present(frontmatter: dict | None, body: str) -> bool:
    """완료 TASK 에 라우팅/eval 기록 또는 범위 사유가 있으면 True."""
    fm = frontmatter or {}
    for key in ("routing_ref", "eval_ref"):
        if str(fm.get(key) or "").strip():
            return True
    return bool(_ROUTING_EVIDENCE_RE.search(body) or _ROUTING_WAIVER_RE.search(body))


def is_iso8601(value: str) -> bool:
    return bool(ISO8601_RE.match(value))


def is_date_or_iso(value: str) -> bool:
    return value == "unknown" or bool(DATE_ONLY_RE.match(value)) or is_iso8601(value)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def latest_cycle_number() -> int | None:
    numbers: list[int] = []
    for path in (ROOT / "agents" / "lead_engineer").glob("CYCLE-*.md"):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers) if numbers else None


def cycle_path(number: int) -> Path:
    return ROOT / "agents" / "lead_engineer" / f"CYCLE-{number:03d}.md"


def review_path(number: int) -> Path:
    return ROOT / "agents" / "lead_engineer" / "reviews" / f"REVIEW-{number:03d}.md"


def extract_status(text: str) -> str:
    match = re.search(r"(?m)^상태:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def coordination_docs() -> list[Path]:
    candidates = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "CLAUDE.md",
        ROOT / "GEMINI.md",
        ROOT / "CURSOR.md",
        ROOT / "agents" / "lead_engineer" / "STATUS.md",
        ROOT / "agents" / "lead_engineer" / "SYSTEM-IMPROVEMENTS-2026-05-20.md",
    ]
    candidates.extend((ROOT / ".github").glob("**/*.md"))
    candidates.extend((ROOT / "docs" / "agent_bootstrap").glob("**/*.md"))
    return [path for path in candidates if path.exists()]


def check_stale_cycle_refs(warnings: list[str]) -> None:
    latest = latest_cycle_number()
    if latest is None:
        warnings.append("No CYCLE-*.md files found under agents/lead_engineer.")
        return

    for path in coordination_docs():
        if path.name == "STATUS.md":
            continue
        text = read_text(path)
        for match in re.finditer(r"CYCLE-(\d{3})", text):
            number = int(match.group(1))
            if number >= latest:
                continue
            # 역사적 attribution (어느 사이클이 무엇을 구현/도입했는가) 은 stale 가 아니다 —
            # 괄호 인용 "(CYCLE-NNN..." 또는 "본 CYCLE-NNN" 백레퍼런스는 면제 (TASK-149, CYCLE-025).
            # §17 등 historical 레저의 과거 사이클 인용이 매 사이클 반복 false-warn 하던 문제 차단.
            before = text[max(0, match.start() - 2):match.start()]
            if before.endswith("(") or before == "본 ":
                continue
            rel = path.relative_to(ROOT)
            warnings.append(
                f"{rel}: stale fixed cycle reference CYCLE-{number:03d}; latest is CYCLE-{latest:03d}."
            )


def task_assignment_files() -> list[Path]:
    task_dir = ROOT / "agents" / "lead_engineer" / "tasks"
    files: list[Path] = []
    for path in task_dir.glob("TASK-*.md"):
        if path.name.endswith("-result.md"):
            continue
        files.append(path)
    return sorted(files)


def task_record_files() -> list[Path]:
    return sorted(ROOT.glob("agents/**/TASK-*.md"))


def extract_task_id_from_filename(path: Path) -> str | None:
    match = re.match(r"^(TASK-\d+)", path.name)
    return match.group(1) if match else None


def task_number(task_id: str | None) -> int | None:
    if not task_id:
        return None
    match = re.match(r"TASK-(\d+)$", task_id)
    return int(match.group(1)) if match else None


def extract_task_id_from_content(text: str) -> str | None:
    match = re.search(r"(?m)^작업 ID:\s*(TASK-\d+)\s*$", text)
    if match:
        return match.group(1)
    match = re.search(r"(?m)^#\s*(TASK-\d+)\b", text)
    return match.group(1) if match else None


def check_tasks(errors: list[str], warnings: list[str]) -> None:
    files = task_assignment_files()
    ids = [extract_task_id_from_filename(path) for path in files]
    duplicate_ids = [task_id for task_id, count in Counter(ids).items() if task_id and count > 1]
    for task_id in duplicate_ids:
        matches = ", ".join(str(path.relative_to(ROOT)) for path in files if extract_task_id_from_filename(path) == task_id)
        errors.append(f"Duplicate task assignment files for {task_id}: {matches}")

    result_files = {path.name.removesuffix("-result.md") for path in ROOT.glob("agents/**/*-result.md")}
    assignment_paths = set(files)
    for path in task_record_files():
        rel = path.relative_to(ROOT)
        text = read_text(path)
        file_task_id = extract_task_id_from_filename(path)
        content_task_id = extract_task_id_from_content(text)
        if file_task_id and content_task_id and file_task_id != content_task_id:
            errors.append(f"{rel}: filename id {file_task_id} does not match content id {content_task_id}.")
        if not content_task_id:
            errors.append(f"{rel}: missing TASK id in content.")

        state_match = re.search(r"(?m)^상태:\s*(.+?)\s*$", text)
        if not state_match:
            errors.append(f"{rel}: missing 상태 field.")
            state = None
        else:
            state = state_match.group(1)
            if state not in ALLOWED_TASK_STATES:
                errors.append(f"{rel}: invalid 상태 '{state}'. Use one of {', '.join(sorted(ALLOWED_TASK_STATES))}.")

        if not re.search(r"(?m)^Owner:\s*\S", text):
            errors.append(f"{rel}: missing Owner field.")

        number = task_number(content_task_id or file_task_id)
        if path in assignment_paths and number is not None and number >= AUDIT_REQUIRED_TASK_NUMBER:
            for field in AUDIT_TASK_FIELDS:
                if not re.search(rf"(?m)^{re.escape(field)}:\s*\S", text):
                    errors.append(f"{rel}: TASK-{number:03d}+ missing audit/objective field '{field}:'.")
            audit_match = re.search(r"(?m)^감사 로그:\s*(AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\s*$", text)
            if not audit_match:
                errors.append(f"{rel}: missing valid audit log id (AUDIT-YYYY-MM-DD-###).")

        if path in assignment_paths and state == "완료":
            has_completion_record = "[작업 완료 기록]" in text or bool(
                re.search(r"(?m)^#+\s*완료 기록\b", text)
            )
            has_result_file = bool(content_task_id and content_task_id in result_files)
            if not has_completion_record and not has_result_file:
                warnings.append(f"{rel}: 상태 is 완료 but no completion record/result file was found.")
            if number is not None and number >= AUDIT_REQUIRED_TASK_NUMBER:
                for field in AUDIT_COMPLETION_FIELDS:
                    if not re.search(rf"(?m)^{re.escape(field)}:\s*\S", text):
                        errors.append(f"{rel}: completed TASK-{number:03d}+ missing completion audit field '{field}:'.")
                for section in ["## 증거", "## 리뷰"]:
                    if section not in text:
                        errors.append(f"{rel}: completed TASK-{number:03d}+ missing section '{section}'.")


def parse_task_registry() -> dict[str, dict[str, str]]:
    registry_path = ROOT / "agents" / "lead_engineer" / "tasks" / "INDEX.md"
    if not registry_path.exists():
        return {}

    rows: dict[str, dict[str, str]] = {}
    text = read_text(registry_path)
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"TASK", "----"}:
            continue
        task_match = re.search(r"(TASK-\d{3})(?!-)", cells[0])
        if not task_match:
            continue
        task_id = task_match.group(1)
        link_match = re.search(r"\[[^\]]+\]\(([^)]+)\)", cells[0])
        rows[task_id] = {
            "status": cells[1],
            "owner": cells[2],
            "target": link_match.group(1) if link_match else "",
            "line": line,
        }
    return rows


def check_task_registry(errors: list[str], warnings: list[str]) -> None:
    registry_path = ROOT / "agents" / "lead_engineer" / "tasks" / "INDEX.md"
    if not registry_path.exists():
        errors.append("agents/lead_engineer/tasks/INDEX.md: missing TASK registry.")
        return

    registry = parse_task_registry()
    if not registry:
        errors.append("agents/lead_engineer/tasks/INDEX.md: no TASK rows found.")
        return

    assignment_statuses: dict[str, str] = {}
    for path in task_assignment_files():
        text = read_text(path)
        task_id = extract_task_id_from_content(text) or extract_task_id_from_filename(path)
        state_match = re.search(r"(?m)^상태:\s*(.+?)\s*$", text)
        if task_id and state_match:
            assignment_statuses[task_id] = state_match.group(1)

    for task_id, state in assignment_statuses.items():
        if task_id not in registry:
            errors.append(f"agents/lead_engineer/tasks/INDEX.md: missing registry row for {task_id}.")
            continue
        registry_state = registry[task_id]["status"]
        if registry_state != state:
            errors.append(
                f"agents/lead_engineer/tasks/INDEX.md: {task_id} status is '{registry_state}' but task file says '{state}'."
            )

    for task_id, row in registry.items():
        state = row["status"]
        if state not in ALLOWED_TASK_STATES:
            errors.append(
                f"agents/lead_engineer/tasks/INDEX.md: {task_id} has invalid status '{state}'."
            )
        target = row["target"]
        if target and task_id not in assignment_statuses:
            warnings.append(
                f"agents/lead_engineer/tasks/INDEX.md: {task_id} links to a TASK file but no assignment file was parsed."
            )


def check_status_doc(errors: list[str]) -> None:
    status_path = ROOT / "agents" / "lead_engineer" / "STATUS.md"
    if not status_path.exists():
        errors.append("agents/lead_engineer/STATUS.md: missing status board.")
        return

    text = read_text(status_path)
    latest = latest_cycle_number()
    if latest is not None and f"CYCLE-{latest:03d}" not in text:
        errors.append(
            f"agents/lead_engineer/STATUS.md: does not reference latest CYCLE-{latest:03d}."
        )
    if "tasks/INDEX.md" not in text:
        errors.append("agents/lead_engineer/STATUS.md: does not reference tasks/INDEX.md.")
    if "reviews/" not in text:
        errors.append("agents/lead_engineer/STATUS.md: does not reference reviews/.")
    if "compound_log.md" not in text:
        errors.append("agents/lead_engineer/STATUS.md: does not reference compound_log.md.")
    if "AUDIT-LOG.md" not in text:
        errors.append("agents/lead_engineer/STATUS.md: does not reference AUDIT-LOG.md.")


CYCLE_TERMINATION_RULE_FROM = 16
INDETERMINATE_TERMINATION_PHRASES = ("잠정", "TBD", "tbd", "협의 후 결정", "대략", "상황에 따라")


CYCLE_REFERENCED_COMPOUNDS_FROM = 23
CYCLE_KEDB_SEARCH_FROM = 32
COMPOUND_FORMAT_V2_FROM = 14
COMPOUND_CATEGORY_ENUM = (
    "misinterpretation",
    "fidelity-violation",
    "process-omission",
    "tooling-defect",
    "coordination-failure",
    "overclaim",
)


def check_cycle_referenced_compounds(errors: list[str], warnings: list[str]) -> None:
    """CYCLE-{NNN}.md 가 `## 참조 Compound` 절을 가지는지 검증 (TASK-146, §17.1).

    Toyota poka-yoke + ITIL KEDB 패턴 — 사이클 진입 시점에 과거 결함을
    명시적으로 인용하도록 강제. LLM 자발 검색에 의존 안 함.
    CYCLE_REFERENCED_COMPOUNDS_FROM 미만 사이클은 legacy 면제.
    """
    cycle_dir = ROOT / "agents" / "lead_engineer"
    for path in sorted(cycle_dir.glob("CYCLE-*.md")):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if not match:
            continue
        number = int(match.group(1))
        if number < CYCLE_REFERENCED_COMPOUNDS_FROM:
            continue

        text = read_text(path)
        header_match = re.search(
            r"^##\s+참조\s*Compound[^\n]*$",
            text,
            re.MULTILINE,
        )
        if not header_match:
            errors.append(
                f"agents/lead_engineer/{path.name}: missing '## 참조 Compound' section (TASK-146 §17.1)."
            )
            continue

        body_start = header_match.end()
        next_header_match = re.search(r"^##\s+", text[body_start:], re.MULTILINE)
        body = (
            text[body_start : body_start + next_header_match.start()]
            if next_header_match
            else text[body_start:]
        )

        if not body.strip():
            errors.append(
                f"agents/lead_engineer/{path.name}: '참조 Compound' section is empty (TASK-146 §17.1)."
            )


def check_cycle_kedb_search(errors: list[str], warnings: list[str]) -> None:
    """CYCLE-{NNN}.md (cutoff=CYCLE_KEDB_SEARCH_FROM+) 가 작업 시작 KEDB 검색을 문서화했는지 검증 (TASK-161, §17.1).

    §17.1 은 작업 시작 시 `kedb_search.py` 로 과거 결함을 surface 하라고 하지만,
    그 수행은 자발(voluntary)이었다. 본 룰은 사이클 doc 에 KEDB 검색 *증거*
    (`kedb_search` 참조 — 실행한 쿼리/결과) 가 있는지 강제해 voluntary → enforced 로 승격.
    `## 참조 Compound` 가 "무엇을 인용했나" 라면, 본 룰은 "검색을 실제로 돌렸나" 를 본다.
    process-omission 반복(COMPOUND-016/017) 의 CAPA — LLM 자발 검색 의존 제거.
    """
    cycle_dir = ROOT / "agents" / "lead_engineer"
    for path in sorted(cycle_dir.glob("CYCLE-*.md")):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if not match:
            continue
        number = int(match.group(1))
        if number < CYCLE_KEDB_SEARCH_FROM:
            continue

        text = read_text(path)
        if "kedb_search" not in text:
            errors.append(
                f"agents/lead_engineer/{path.name}: missing KEDB search evidence "
                f"(작업 시작 KEDB 검색 — `kedb_search.py ...` 참조 필요, §17.1 enforced)."
            )


def check_compound_format_v2(errors: list[str], warnings: list[str]) -> None:
    """compound_log.md 의 COMPOUND-{NNN} (cutoff=14+) 가 v2 형식인지 검증 (TASK-146, §17.3/17.4/17.6).

    필수 필드: 카테고리 enum + 5 Whys 본문 (최소 3 단계) + 검증 방법 절.
    COMPOUND_FORMAT_V2_FROM 미만 entry 는 legacy 면제.
    """
    log_path = ROOT / "agents" / "lead_engineer" / "compound_log.md"
    if not log_path.exists():
        return

    text = read_text(log_path)
    # COMPOUND entry 분리 — ### COMPOUND-NNN ~ 다음 ### 또는 EOF
    entries = re.split(r"(?=^### COMPOUND-\d+\s*$)", text, flags=re.MULTILINE)
    for entry in entries:
        m = re.match(r"### COMPOUND-(\d+)\s*$", entry.strip().split("\n", 1)[0])
        if not m:
            continue
        number = int(m.group(1))
        if number < COMPOUND_FORMAT_V2_FROM:
            continue

        # 카테고리 필드
        cat_match = re.search(r"^카테고리:\s*(\S+)\s*$", entry, re.MULTILINE)
        if not cat_match:
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: missing '카테고리:' field (TASK-146 §17.3)."
            )
        elif cat_match.group(1) not in COMPOUND_CATEGORY_ENUM:
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: invalid 카테고리 '{cat_match.group(1)}'. "
                f"Use one of {list(COMPOUND_CATEGORY_ENUM)}."
            )

        # 재발 횟수 필드
        if not re.search(r"^재발 횟수:\s*\d+\s*$", entry, re.MULTILINE):
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: missing '재발 횟수:' field (TASK-146 §17.3)."
            )

        # 5 Whys 절
        if "#### 5 Whys" not in entry:
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: missing '#### 5 Whys' section (TASK-146 §17.4)."
            )
        else:
            # 최소 3 단계 (1. / 2. / 3. 형태)
            whys_section_match = re.search(
                r"#### 5 Whys(.*?)(?=^####\s|^### |$\Z)",
                entry,
                re.MULTILINE | re.DOTALL,
            )
            if whys_section_match:
                whys_body = whys_section_match.group(1)
                step_count = len(re.findall(r"^\s*(\d+)\.\s+왜", whys_body, re.MULTILINE))
                if step_count < 3:
                    errors.append(
                        f"compound_log.md COMPOUND-{number:03d}: '5 Whys' section has only "
                        f"{step_count} steps (minimum 3 required, TASK-146 §17.4)."
                    )

        # 검증 방법 절
        if "#### 검증 방법" not in entry:
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: missing '#### 검증 방법' section (TASK-146 §17.6)."
            )


# ---- TASK-151 (CYCLE-024) — RETRO/REVIEW/COMPOUND 강제 강화 (§17.7/17.9/17.10) ----

REVIEW_REFERENCED_COMPOUNDS_FROM = 24
# TASK-204 (CYCLE-072): REVIEW 가 협업(§16)을 명시적으로 다뤘는지 강제. §16 이 "권고"라 Lead
# self-review 로 회피돼 온 것을 닫는다. cutoff 이상 REVIEW 는 '## 협업' 절에 subagent dispatch
# 증거(collab 로그/이벤트/dispatch) 또는 명시적 waiver(예: "self — Low, 사유 …") 를 적어야 한다.
REVIEW_COLLABORATION_FROM = 72
REVIEW_COLLABORATION_EVIDENCE_RE = re.compile(
    r"(subagent|multi_agent_v1|codex_subagent|collab[_ -]?log|"
    r"subagent[_ -]?dispatch|council|waiver|면제|생략|self\s*[-—]\s*Low)",
    re.I,
)
RECURRENCE_ESCALATION_THRESHOLD = 3
RETRO_FORWARD_TRACKING_FROM = "2026-05-29"


def _section_body(text: str, header_re: str) -> str | None:
    """Return the body between a `## <header>` and the next `## ` header (or EOF)."""
    m = re.search(header_re, text, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^##\s+", text[start:], re.MULTILINE)
    return text[start : start + nxt.start()] if nxt else text[start:]


def check_review_referenced_compounds(errors: list[str], warnings: list[str]) -> None:
    """REVIEW-{NNN}.md (cutoff=24+) 가 CYCLE 와 동급 strict gate 를 가지는지 (§17.10).

    - `## 참조 Compound` 절 존재 (CYCLE §17.1 parity — 본 사이클이 해소/발급한 compound 인용)
    - `## Compound 필요 여부` 가 명시적 Y 또는 N
    REVIEW_REFERENCED_COMPOUNDS_FROM 미만은 legacy 면제.
    """
    review_dir = ROOT / "agents" / "lead_engineer" / "reviews"
    if not review_dir.exists():
        return
    for path in sorted(review_dir.glob("REVIEW-*.md")):
        m = re.match(r"REVIEW-(\d+)\.md$", path.name)
        if not m:
            continue
        number = int(m.group(1))
        if number < REVIEW_REFERENCED_COMPOUNDS_FROM:
            continue

        text = read_text(path)
        ref_body = _section_body(text, r"^##\s+참조\s*Compound[^\n]*$")
        if ref_body is None:
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: missing '## 참조 Compound' section (§17.10)."
            )
        elif not ref_body.strip():
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: '참조 Compound' section is empty (§17.10)."
            )

        cf_body = _section_body(text, r"^##\s+Compound 필요 여부[^\n]*$")
        if cf_body is not None and not re.search(r"(?m)^\s*-?\s*[YN]\b", cf_body):
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: 'Compound 필요 여부' must be explicit Y or N (§17.10)."
            )


def check_review_collaboration(errors: list[str], warnings: list[str]) -> None:
    """REVIEW-{NNN}.md (cutoff=REVIEW_COLLABORATION_FROM+) 가 '## 협업' 절을 가지는지 (TASK-204, §16).

    §16 협업 의무가 "권고"라 Lead self-review 로 반복 회피된 것을 닫는 forcing function.
    협업 절은 subagent dispatch 증거(collab_log/이벤트/dispatch) 또는 명시적 waiver 를 담아야 한다.
    cutoff 미만 REVIEW 는 legacy 면제(소급 안 함).
    """
    review_dir = ROOT / "agents" / "lead_engineer" / "reviews"
    if not review_dir.exists():
        return
    for path in sorted(review_dir.glob("REVIEW-*.md")):
        m = re.match(r"REVIEW-(\d+)\.md$", path.name)
        if not m:
            continue
        number = int(m.group(1))
        if number < REVIEW_COLLABORATION_FROM:
            continue
        text = read_text(path)
        body = _section_body(text, r"^##\s+협업[^\n]*$")
        if body is None:
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: missing '## 협업' section "
                f"(TASK-204/§16 — subagent dispatch 증거 또는 명시 waiver 필요)."
            )
        elif not body.strip():
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: '협업' section is empty (TASK-204/§16)."
            )
        elif not REVIEW_COLLABORATION_EVIDENCE_RE.search(body):
            errors.append(
                f"agents/lead_engineer/reviews/{path.name}: '협업' section must include "
                "subagent/collab evidence or explicit waiver (TASK-204/§16)."
            )


def check_compound_recurrence_escalation(errors: list[str], warnings: list[str]) -> None:
    """재발 횟수>=3 COMPOUND 가 critical escalation 마커를 가지는지 (§17.3).

    ITIL KEDB — 반복 결함은 escalation. 마커 누락 시 ERROR.
    """
    log_path = ROOT / "agents" / "lead_engineer" / "compound_log.md"
    if not log_path.exists():
        return
    text = read_text(log_path)
    entries = re.split(r"(?=^### COMPOUND-\d+\s*$)", text, flags=re.MULTILINE)
    for entry in entries:
        m = re.match(r"### COMPOUND-(\d+)\s*$", entry.strip().split("\n", 1)[0])
        if not m:
            continue
        number = int(m.group(1))
        rec = re.search(r"^재발 횟수:\s*(\d+)\s*$", entry, re.MULTILINE)
        if not rec:
            continue
        count = int(rec.group(1))
        if count >= RECURRENCE_ESCALATION_THRESHOLD and "critical" not in entry.lower():
            errors.append(
                f"compound_log.md COMPOUND-{number:03d}: 재발 횟수 {count} >= "
                f"{RECURRENCE_ESCALATION_THRESHOLD} but no 'critical' escalation marker (§17.3)."
            )


def check_retro_forward_actions(errors: list[str], warnings: list[str]) -> None:
    """RETRO §5 Forward Actions 의 'TASK 후보' 행이 추적 가능한지 (§17.7 teeth).

    COMPOUND-016 근본 원인 = Forward Actions 가 명시만 되고 추적 안 됨 → 6+ 재발.
    cutoff: recorded_at >= RETRO_FORWARD_TRACKING_FROM (legacy RETRO 면제).
    'TASK 후보' 행 제안에 TASK-/BTC-/BUG- id 가 없으면 추적 불가 → WARN.
    """
    agents_root = ROOT / "agents"
    if not agents_root.exists():
        return
    for path in sorted(agents_root.glob("*/retros/RETRO-*.md")):
        if path.name == "TEMPLATE.md":
            continue
        text = read_text(path)
        fm = parse_frontmatter(text)
        if fm is None:
            continue
        recorded = str(fm.get("recorded_at", ""))[:10]
        if not recorded or recorded < RETRO_FORWARD_TRACKING_FROM:
            continue  # legacy 면제

        body = _section_body(text, r"^##\s+§5\s+Forward Actions[^\n]*$")
        if body is None:
            continue  # check_retros 가 별도로 §5 존재 강제
        rel = path.relative_to(ROOT)
        for line in body.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            kind, proposal = cells[0], cells[1]
            if "TASK 후보" not in kind:
                continue
            if not re.search(r"\b(TASK|BTC|BUG)-\d+", proposal):
                warnings.append(
                    f"{rel}: §5 Forward Action 'TASK 후보' not trackable "
                    f"(no TASK-/BTC-/BUG- id): '{proposal[:40]}' (§17.7)."
                )


def check_cycle_termination_conditions(errors: list[str], warnings: list[str]) -> None:
    """CYCLE-{NNN}.md 가 결정형 §본 사이클 종료 조건 절을 가지는지 검증 (TASK-139, COMPOUND-009).

    결정형 = 불릿 라인 1+ + 미확정 표현(잠정/TBD/협의 후 결정/대략/상황에 따라) 부재.
    CYCLE_TERMINATION_RULE_FROM 미만 사이클은 legacy 면제 (COMPOUND-006 cutoff 정책).
    """
    cycle_dir = ROOT / "agents" / "lead_engineer"
    for path in sorted(cycle_dir.glob("CYCLE-*.md")):
        match = re.match(r"CYCLE-(\d+)\.md$", path.name)
        if not match:
            continue
        number = int(match.group(1))
        if number < CYCLE_TERMINATION_RULE_FROM:
            continue

        text = read_text(path)
        header_match = re.search(
            r"^##\s+본\s*사이클\s*종료\s*조건[^\n]*$",
            text,
            re.MULTILINE,
        )
        if not header_match:
            errors.append(
                f"agents/lead_engineer/{path.name}: missing '## 본 사이클 종료 조건' section (TASK-139)."
            )
            continue

        body_start = header_match.end()
        next_header_match = re.search(r"^##\s+", text[body_start:], re.MULTILINE)
        body = (
            text[body_start : body_start + next_header_match.start()]
            if next_header_match
            else text[body_start:]
        )

        bullet_lines = [
            line for line in body.splitlines() if line.strip().startswith("-")
        ]
        if len(bullet_lines) == 0:
            errors.append(
                f"agents/lead_engineer/{path.name}: '본 사이클 종료 조건' has no bullet items (decision-form requires 1+ bullets)."
            )

        for phrase in INDETERMINATE_TERMINATION_PHRASES:
            if phrase in body:
                errors.append(
                    f"agents/lead_engineer/{path.name}: '본 사이클 종료 조건' contains indeterminate phrase '{phrase}' (TASK-139)."
                )


def check_reviews(errors: list[str], warnings: list[str]) -> None:
    latest = latest_cycle_number()
    review_dir = ROOT / "agents" / "lead_engineer" / "reviews"
    if latest is None:
        return
    if not review_dir.exists():
        errors.append("agents/lead_engineer/reviews: missing review directory.")
        return

    cycles_to_check = {latest}
    if latest > 1:
        cycles_to_check.add(latest - 1)

    for number in sorted(cycles_to_check):
        c_path = cycle_path(number)
        if not c_path.exists():
            continue
        cycle_text = read_text(c_path)
        status = extract_status(cycle_text)
        r_path = review_path(number)
        has_inline_review = "[REVIEW]" in cycle_text
        if "완료" in status and not r_path.exists() and not has_inline_review:
            errors.append(
                f"agents/lead_engineer/reviews/REVIEW-{number:03d}.md: missing review for completed CYCLE-{number:03d}."
            )
        elif "부분 완료" in status and not r_path.exists() and not has_inline_review:
            warnings.append(
                f"agents/lead_engineer/reviews/REVIEW-{number:03d}.md: no interim review for partially completed CYCLE-{number:03d}."
            )

    required_sections = [
        "## 완료 항목",
        "## 미완료/이월 항목",
        "## 회귀 리스크",
        "## Compound 필요 여부",
    ]
    for path in sorted(review_dir.glob("REVIEW-*.md")):
        text = read_text(path)
        rel = path.relative_to(ROOT)
        if "[REVIEW]" not in text:
            errors.append(f"{rel}: missing [REVIEW] header.")
        for section in required_sections:
            if section not in text:
                errors.append(f"{rel}: missing section '{section}'.")


def check_compound_log(errors: list[str], warnings: list[str]) -> None:
    path = ROOT / "agents" / "lead_engineer" / "compound_log.md"
    if not path.exists():
        errors.append("agents/lead_engineer/compound_log.md: missing compound log.")
        return

    text = read_text(path)
    entries = list(re.finditer(r"(?m)^### COMPOUND-(\d+)\s*$", text))
    if not entries:
        warnings.append("agents/lead_engineer/compound_log.md: no COMPOUND entries found.")
        return

    required_fields = ["날짜:", "발견한 패턴:", "근본 원인:", "개선 조치:", "적용 대상:", "상태:"]
    for index, match in enumerate(entries):
        start = match.end()
        end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
        block = text[start:end]
        compound_id = match.group(1)
        if int(compound_id) >= COMPOUND_FORMAT_V2_FROM:
            continue  # v2 (TASK-146 §17) — separate check_compound_format_v2 handles these
        for field in required_fields:
            if field not in block:
                errors.append(f"agents/lead_engineer/compound_log.md: COMPOUND-{compound_id} missing '{field}' field.")
        if int(compound_id) >= 4:
            for field in ["기록 시각:", "감사 로그:"]:
                if field not in block:
                    errors.append(f"agents/lead_engineer/compound_log.md: COMPOUND-{compound_id} missing '{field}' field.")

    review_dir = ROOT / "agents" / "lead_engineer" / "reviews"
    if review_dir.exists():
        compound_required = False
        for review in review_dir.glob("REVIEW-*.md"):
            review_text = read_text(review)
            if "## Compound 필요 여부" in review_text and re.search(r"(?m)^-\s*Y\s*$", review_text):
                compound_required = True
                break
        if compound_required and len(entries) < 2:
            warnings.append(
                "agents/lead_engineer/compound_log.md: reviews require Compound but only the initial entry exists."
            )


def check_audit_log(errors: list[str], warnings: list[str]) -> None:
    path = ROOT / "agents" / "lead_engineer" / "AUDIT-LOG.md"
    if not path.exists():
        errors.append("agents/lead_engineer/AUDIT-LOG.md: missing audit log.")
        return

    text = read_text(path)
    body = text.split("## 기록", 1)[1] if "## 기록" in text else text
    entries = list(re.finditer(r"(?m)^### (AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\s*$", body))
    if not entries:
        errors.append("agents/lead_engineer/AUDIT-LOG.md: no AUDIT entries found.")
        return

    for index, match in enumerate(entries):
        start = match.end()
        end = entries[index + 1].start() if index + 1 < len(entries) else len(body)
        block = body[start:end]
        audit_id = match.group(1)
        for field in AUDIT_LOG_FIELDS:
            if field not in block:
                errors.append(f"agents/lead_engineer/AUDIT-LOG.md: {audit_id} missing '{field}' field.")

    known_audit_ids = {match.group(1) for match in entries}
    for task_path in task_assignment_files():
        text = read_text(task_path)
        task_id = extract_task_id_from_content(text) or extract_task_id_from_filename(task_path)
        number = task_number(task_id)
        if number is None or number < AUDIT_REQUIRED_TASK_NUMBER:
            continue
        audit_match = re.search(r"(?m)^감사 로그:\s*(AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\s*$", text)
        if audit_match and audit_match.group(1) not in known_audit_ids:
            rel = task_path.relative_to(ROOT)
            errors.append(f"{rel}: references unknown audit id {audit_match.group(1)}.")


def markdown_files() -> list[Path]:
    ignored_parts = {".git", "node_modules", ".tmp", "build", "__pycache__"}
    ignored_prefixes = (
        "packages/ralph-automation/src/ralph_automation/templates/",
        "packages/ralph-automation/templates/",
    )
    return sorted(
        path
        for path in ROOT.glob("**/*.md")
        if not any(part in ignored_parts for part in path.parts)
        and not path.relative_to(ROOT).as_posix().startswith(ignored_prefixes)
    )


def normalize_link_target(target: str) -> str:
    target = target.strip()
    if not target:
        return target
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if " " in target:
        target = target.split(" ", 1)[0]
    target = unquote(target)
    if "#" in target:
        target = target.split("#", 1)[0]
    target = re.sub(r":\d+$", "", target)
    return target


def check_markdown_links(warnings: list[str]) -> None:
    link_re = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for path in markdown_files():
        text = read_text(path)
        for match in link_re.finditer(text):
            target = normalize_link_target(match.group(1))
            if not target or target.startswith("#"):
                continue
            lowered = target.lower()
            if lowered.startswith(SKIP_LINK_SCHEMES):
                continue
            target_path = (path.parent / target).resolve()
            try:
                target_path.relative_to(ROOT)
            except ValueError:
                continue
            if not target_path.exists():
                rel = path.relative_to(ROOT)
                warnings.append(f"{rel}: Markdown link target not found: {match.group(1)}")


def check_task_frontmatter(errors: list[str], warnings: list[str]) -> None:
    """Validate YAML frontmatter in TASK files (TASK-048+)."""
    audit_log_path = ROOT / "agents" / "lead_engineer" / "AUDIT-LOG.md"
    known_audit_ids: set = set()
    if audit_log_path.exists():
        for m in re.finditer(r"(?m)^### (AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\s*$", read_text(audit_log_path)):
            known_audit_ids.add(m.group(1))

    meetings_dir = ROOT / "agents" / "lead_engineer" / "meetings"
    known_meeting_ids: set = set()
    if meetings_dir.exists():
        for path in meetings_dir.glob("MEETING-*.md"):
            match = re.match(r"^(MEETING-\d{4}-\d{2}-\d{2}-\d{3})\.md$", path.name)
            if match:
                known_meeting_ids.add(match.group(1))

    required_keys = [
        "type", "id", "status", "owner", "priority", "difficulty",
        "est_hours", "est_tokens", "tags", "audit_log", "created",
    ]

    for path in task_assignment_files():
        text = read_text(path)
        file_task_id = extract_task_id_from_filename(path)
        number = task_number(file_task_id)
        if number is None:
            continue
        fm = parse_frontmatter(text)
        rel = path.relative_to(ROOT)

        if number >= FRONTMATTER_REQUIRED_TASK_NUMBER:
            if fm is None:
                errors.append(f"{rel}: TASK-{number:03d}+ missing YAML frontmatter.")
                continue
            for key in required_keys:
                if key not in fm or fm[key] in (None, "", []):
                    errors.append(f"{rel}: frontmatter missing required key '{key}'.")
            if fm.get("type") not in {"task"}:
                errors.append(f"{rel}: frontmatter 'type' must be 'task' (got '{fm.get('type')}').")
            if not TASK_ID_RE.match(str(fm.get("id", ""))):
                errors.append(f"{rel}: frontmatter 'id' must match TASK-NNN format.")
            elif fm["id"] != file_task_id:
                errors.append(f"{rel}: frontmatter id '{fm['id']}' != filename id '{file_task_id}'.")
            body_status = extract_status(text)
            if fm.get("status") != body_status:
                errors.append(f"{rel}: frontmatter status '{fm.get('status')}' != body 상태 '{body_status}'.")
            if fm.get("status") not in ALLOWED_TASK_STATES:
                errors.append(f"{rel}: invalid frontmatter status '{fm.get('status')}'.")
            owner_match = re.search(r"(?m)^Owner:\s*(.+?)\s*$", text)
            body_owner = owner_match.group(1).strip() if owner_match else ""
            if body_owner and fm.get("owner") and fm["owner"] != body_owner:
                errors.append(f"{rel}: frontmatter owner '{fm['owner']}' != body Owner '{body_owner}'.")
            if fm.get("priority") not in ALLOWED_PRIORITY:
                errors.append(f"{rel}: invalid priority '{fm.get('priority')}'. Use one of {sorted(ALLOWED_PRIORITY)}.")
            if fm.get("difficulty") not in ALLOWED_DIFFICULTY:
                errors.append(f"{rel}: invalid difficulty '{fm.get('difficulty')}'. Use one of {sorted(ALLOWED_DIFFICULTY)}.")
            if not isinstance(fm.get("tags"), list):
                errors.append(f"{rel}: frontmatter 'tags' must be a list.")
            try:
                float(str(fm.get("est_hours", "")))
            except (TypeError, ValueError):
                errors.append(f"{rel}: frontmatter 'est_hours' must be numeric.")
            try:
                int(str(fm.get("est_tokens", "")))
            except (TypeError, ValueError):
                errors.append(f"{rel}: frontmatter 'est_tokens' must be an integer.")
            if not AUDIT_ID_RE.match(str(fm.get("audit_log", ""))):
                errors.append(f"{rel}: frontmatter 'audit_log' must match AUDIT-YYYY-MM-DD-###.")
            elif known_audit_ids and fm["audit_log"] not in known_audit_ids:
                errors.append(f"{rel}: frontmatter references unknown audit_log '{fm['audit_log']}'.")
            created = str(fm.get("created", ""))
            if not is_date_or_iso(created):
                errors.append(f"{rel}: frontmatter 'created' must be YYYY-MM-DD or ISO 8601.")
            for ts_field in ("created_at", "started_at", "completed_at"):
                if ts_field in fm and fm[ts_field]:
                    if fm[ts_field] != "unknown" and not is_iso8601(str(fm[ts_field])):
                        errors.append(f"{rel}: frontmatter '{ts_field}' must be ISO 8601 (got '{fm[ts_field]}').")
            tm = fm.get("trigger_meeting")
            if tm and tm != "자가발생" and known_meeting_ids and tm not in known_meeting_ids:
                errors.append(f"{rel}: frontmatter trigger_meeting '{tm}' not found in meetings/.")
        else:
            if fm is not None:
                if "id" in fm and fm["id"] != file_task_id:
                    errors.append(f"{rel}: legacy file has frontmatter with mismatched id.")


def check_meeting_frontmatter(errors: list[str], warnings: list[str]) -> None:
    """Validate YAML frontmatter in MEETING files."""
    meetings_dir = ROOT / "agents" / "lead_engineer" / "meetings"
    if not meetings_dir.exists():
        return

    required_keys = [
        "type", "id", "date", "meeting_type", "requester", "facilitator",
        "audit_log", "tags",
    ]
    required_sections = [
        "## 사용자 원문 요청",
        "## 결정 사항",
        "## 도출된 작업",
    ]

    audit_log_path = ROOT / "agents" / "lead_engineer" / "AUDIT-LOG.md"
    known_audit_ids: set = set()
    if audit_log_path.exists():
        for m in re.finditer(r"(?m)^### (AUDIT-\d{4}-\d{2}-\d{2}-\d{3})\s*$", read_text(audit_log_path)):
            known_audit_ids.add(m.group(1))

    for path in sorted(meetings_dir.glob("MEETING-*.md")):
        text = read_text(path)
        rel = path.relative_to(ROOT)
        fm = parse_frontmatter(text)
        file_id_match = re.match(r"^(MEETING-\d{4}-\d{2}-\d{2}-\d{3})\.md$", path.name)
        file_id = file_id_match.group(1) if file_id_match else None

        if fm is None:
            errors.append(f"{rel}: meeting file missing YAML frontmatter.")
            continue
        for key in required_keys:
            if key not in fm or fm[key] in (None, "", []):
                errors.append(f"{rel}: meeting frontmatter missing key '{key}'.")
        if fm.get("type") != "meeting":
            errors.append(f"{rel}: meeting frontmatter 'type' must be 'meeting'.")
        if not MEETING_ID_RE.match(str(fm.get("id", ""))):
            errors.append(f"{rel}: meeting 'id' must match MEETING-YYYY-MM-DD-### format.")
        elif file_id and fm["id"] != file_id:
            errors.append(f"{rel}: meeting frontmatter id '{fm['id']}' != filename id '{file_id}'.")
        if not DATE_ONLY_RE.match(str(fm.get("date", ""))):
            errors.append(f"{rel}: meeting 'date' must be YYYY-MM-DD.")
        if fm.get("meeting_type") not in ALLOWED_MEETING_TYPES:
            errors.append(f"{rel}: meeting_type must be one of {sorted(ALLOWED_MEETING_TYPES)}.")
        for ts_field in ("started_at", "ended_at", "recorded_at"):
            if ts_field in fm and fm[ts_field]:
                if fm[ts_field] != "unknown" and not is_iso8601(str(fm[ts_field])):
                    errors.append(f"{rel}: meeting '{ts_field}' must be ISO 8601 or 'unknown'.")
        if not AUDIT_ID_RE.match(str(fm.get("audit_log", ""))):
            errors.append(f"{rel}: meeting 'audit_log' must match AUDIT-YYYY-MM-DD-###.")
        elif known_audit_ids and fm["audit_log"] not in known_audit_ids:
            errors.append(f"{rel}: meeting references unknown audit_log '{fm['audit_log']}'.")
        if not isinstance(fm.get("tags"), list):
            errors.append(f"{rel}: meeting 'tags' must be a list.")
        if "derived_tasks" in fm and not isinstance(fm["derived_tasks"], list):
            errors.append(f"{rel}: meeting 'derived_tasks' must be a list.")
        # 비자명 결정 미팅은 research evidence 기록 강제 (forcing function, EVIDENCE-2026-06-01-002).
        # "비자명" = derived_tasks≥1 (trivial 질의/코드수정은 derived_tasks 없음 → 면제).
        # date >= cutoff 이면 'evidence:' 필드 필수(EVIDENCE-<id> 또는 '불요 — 사유'). 누락 = ERROR.
        # ERROR 승격(MEETING-2026-06-01-003): WARN 은 무시되기 쉬워 default-on 효과 약함.
        # 불요-escape 로 저마찰 유지(의식적 skip 만 강제). cutoff 이전 미팅 면제(소급 안 함).
        EVIDENCE_CUTOFF = "2026-06-01"
        m_date = str(fm.get("date", ""))
        has_derived = isinstance(fm.get("derived_tasks"), list) and len(fm["derived_tasks"]) > 0
        if m_date >= EVIDENCE_CUTOFF and has_derived:
            ev = fm.get("evidence")
            if not ev or str(ev).strip() == "":
                errors.append(
                    f"{rel}: 비자명 결정 미팅(derived_tasks≥1)에 'evidence:' 필드 누락 — "
                    f"EVIDENCE-<id> 또는 '불요 — 사유' 필요 (default-on opt-out, EVIDENCE-2026-06-01-002)."
                )
        if fm.get("meeting_type") in {"분석", "기획"}:
            for section in required_sections + ["## 의견 / 논거", "## 결정 사유"]:
                if section not in text:
                    errors.append(f"{rel}: missing required section '{section}' (analysis/planning meeting).")
        else:
            for section in required_sections:
                if section not in text:
                    errors.append(f"{rel}: missing required section '{section}'.")


REPORT_KIND_VALUES = {"BRIEF", "PLAN"}
REPORT_AUDIENCE_VALUES = {"Owner", "CEO", "agent", "mixed"}
REPORT_SCALE_VALUES = {"mini", "standard", "full"}
# TASK-124 후속(CYCLE-063): CEO/Owner-facing BRIEF/PLAN 은 Executive Layer 마커(`Bottom Line:`)로
# 시작해야 한다(CLAUDE.md §5.3 / REPORTING-FORMAT.md §보고 2-layer). always-loaded 문서에 트리거가
# 있어도 반복 누락돼 온 규칙을 forcing function(process gate)으로 강제. 소급 안 함(cutoff 이전 면제).
REPORT_EXECUTIVE_LAYER_FROM = "2026-06-02"
REPORT_EXECUTIVE_AUDIENCES = {"CEO", "Owner", "mixed"}
REPORT_BOTTOM_LINE_RE = re.compile(r"(?mi)^\s*(?:\*\*|__)?\s*bottom line\s*(?:\*\*|__)?\s*:")
REPORT_ID_RE = re.compile(r"^(BRIEF|PLAN)-(\d{4}-\d{2}-\d{2})-(\d{3})$")
# REPORTING-FORMAT.md §이모지정책: BRIEF/PLAN 은 *장식용* 이모지를 쓰지 않는다.
# 단, O/X/체크박스 같은 간단·직관적 *상태 표시* 글리프는 허용 (CEO 결정 2026-05-27).
# pictographic/dingbat/symbol 블록을 검출하되, 아래 허용 글리프는 제외한다.
REPORT_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF]"
)
# 허용되는 간단한 상태 마커 (check / cross / ballot box / circle 계열).
REPORT_ALLOWED_STATUS_GLYPHS = {
    "✓",  # ✓ check mark
    "✔",  # ✔ heavy check mark
    "✗",  # ✗ ballot x
    "✘",  # ✘ heavy ballot x
    "✅",  # ✅ check mark button
    "❌",  # ❌ cross mark
    "☑",  # ☑ ballot box with check
    "☐",  # ☐ ballot box
    "☒",  # ☒ ballot box with x
    "⭕",  # ⭕ heavy large circle (O)
}
# 허용 글리프를 너무 많이 쓰면 "남발" 로 보아 경고 (장식 회귀 방지).
REPORT_STATUS_GLYPH_WARN_THRESHOLD = 20
REPORT_REQUIRED_FIELDS = [
    "type", "id", "kind", "date", "recorded_at",
    "audience", "scale", "title", "author",
    "insights_count", "decisions_count",
]


HANDOFF_HEADER_RE = re.compile(r"^##\s+Handoff\b", re.MULTILINE)
HANDOFF_REQUIRED_KEYWORDS = [
    # (logical name, list of acceptable alternate phrasings — any one match counts)
    ("현재 상태", ["현재 상태"]),
    ("검증한 명령", ["검증한 명령", "검증 명령", "확인한 명령"]),
    ("다음 모델 행동", ["다음 모델", "다음 단계", "다음 작업"]),
    ("주의", ["주의"]),
]
BOOTSTRAP_KEYWORDS = [
    "AGENTS.md",
    "STATUS.md",
    "AUDIT-LOG.md",
    "tasks/INDEX.md",
    "CYCLE-",
    "RETRO-",
    "Context Snapshot",
    "check_agent_docs.py",
]
BOOTSTRAP_FILES = ["claude.md", "codex.md", "gemini.md", "cursor.md"]


def check_handoff_sections(errors: list[str], warnings: list[str]) -> None:
    """TASK-092 Handoff Protocol validation.

    Any TASK file containing a `## Handoff` header must include all 4
    required section keywords (AGENTS.md §13.2). Missing any of them
    is an ERROR.
    """
    for path in task_assignment_files():
        text = read_text(path)
        if not HANDOFF_HEADER_RE.search(text):
            continue
        rel = path.relative_to(ROOT)
        # Examine only the body from the first Handoff header onward.
        match = HANDOFF_HEADER_RE.search(text)
        body = text[match.start():] if match else text
        missing: list[str] = []
        for logical_name, alternates in HANDOFF_REQUIRED_KEYWORDS:
            if not any(alt in body for alt in alternates):
                missing.append(logical_name)
        if missing:
            errors.append(
                f"{rel}: Handoff section missing required keywords {missing} "
                f"(AGENTS.md §13.2). Acceptable variants in HANDOFF_REQUIRED_KEYWORDS."
            )


def check_bootstrap_sync(errors: list[str], warnings: list[str]) -> None:
    """TASK-092 bootstrap 4-way sync validation.

    Every docs/agent_bootstrap/{claude,codex,gemini,cursor}.md must mention
    each of the 8 core keywords (AGENTS.md §13.4). Missing any = ERROR.

    Lets one model's docs drift without the others noticing be impossible.
    """
    bootstrap_dir = ROOT / "docs" / "agent_bootstrap"
    if not bootstrap_dir.exists():
        return
    for name in BOOTSTRAP_FILES:
        path = bootstrap_dir / name
        if not path.exists():
            errors.append(f"docs/agent_bootstrap/{name} missing (AGENTS.md §13.4).")
            continue
        text = read_text(path)
        missing: list[str] = [kw for kw in BOOTSTRAP_KEYWORDS if kw not in text]
        if missing:
            errors.append(
                f"docs/agent_bootstrap/{name}: missing required keywords {missing} "
                f"(AGENTS.md §13.4)."
            )


RETRO_REQUIRED_FRONTMATTER = ["type", "id", "role", "period_start", "period_end", "recorded_at"]
RETRO_REQUIRED_SECTIONS = [
    "## §1 Planned vs Actual",
    "## §2 Root Cause",
    "## §3 Collaboration Health Check",
    "## §4 Feedforward",
    "## §5 Forward Actions",
]
RETRO_ID_RE = re.compile(r"^RETRO-[a-z][a-z0-9-]*-\d{4}-\d{2}-\d{2}$")


def check_retros(errors: list[str], warnings: list[str]) -> None:
    """TASK-067 RETRO 자동 검증.

    Checks every agents/{role}/retros/RETRO-{role}-YYYY-MM-DD.md:
      - frontmatter required fields (type/id/role/period_start/period_end/recorded_at)
      - body contains all 5 required section headers (§1~§5)
      - id matches filename
      - type == "retro"
      - dates valid (period_start <= period_end)
      - §5 Forward Actions table header unchanged (TASK-068 auto-parsing)
    """
    import datetime as _dt
    retro_dir = ROOT / "agents"
    if not retro_dir.exists():
        return
    for path in sorted(retro_dir.glob("*/retros/RETRO-*.md")):
        rel = path.relative_to(ROOT)
        if path.name == "TEMPLATE.md":
            continue
        text = read_text(path)
        fm = parse_frontmatter(text)
        if fm is None:
            errors.append(f"{rel}: RETRO missing YAML frontmatter.")
            continue
        for key in RETRO_REQUIRED_FRONTMATTER:
            if key not in fm or fm[key] in (None, "", []):
                errors.append(f"{rel}: RETRO frontmatter missing key '{key}'.")
        if fm.get("type") != "retro":
            errors.append(f"{rel}: RETRO 'type' must be 'retro'.")
        rid = str(fm.get("id", ""))
        if not RETRO_ID_RE.match(rid):
            errors.append(f"{rel}: RETRO 'id' must match RETRO-{{role}}-YYYY-MM-DD (got '{rid}').")
        elif path.stem != rid:
            errors.append(f"{rel}: filename stem '{path.stem}' != frontmatter id '{rid}'.")

        # date sanity
        ps, pe = fm.get("period_start", ""), fm.get("period_end", "")
        if DATE_ONLY_RE.match(str(ps)) and DATE_ONLY_RE.match(str(pe)):
            try:
                ds = _dt.date.fromisoformat(str(ps))
                de = _dt.date.fromisoformat(str(pe))
                if ds > de:
                    errors.append(f"{rel}: period_start '{ps}' > period_end '{pe}'.")
            except ValueError:
                pass

        for section in RETRO_REQUIRED_SECTIONS:
            if section not in text:
                errors.append(f"{rel}: missing required section '{section}' (RETRO 6종 조합 포맷).")

        # TASK-068 — §5 Forward Actions table header가 변경되지 않았는지 (자동 파싱 의존)
        forward_idx = text.find("## §5 Forward Actions")
        if forward_idx != -1:
            section_body = text[forward_idx:forward_idx + 1000]  # 앞부분만 검사
            expected_header = "| 종류 | 제안 | 우선순위 | Owner 제안 | 근거 |"
            if expected_header not in section_body:
                warnings.append(
                    f"{rel}: §5 Forward Actions table header changed "
                    f"(expected '{expected_header}' — TASK-068 auto-parsing 깨질 위험)"
                )


def check_token_budget(errors: list[str], warnings: list[str]) -> None:
    """TASK-093 Session Budget Protocol — TOKEN-BUDGET.md 무결성.

    Checks:
      - agents/lead_engineer/TOKEN-BUDGET.md exists (else WARN — optional pre §14)
      - newest date in the file is within 6 months (else WARN — stale catalog)
      - AGENTS.md §14 cross-links TOKEN-BUDGET.md (else ERROR — broken protocol)
    """
    import datetime as _dt

    budget_path = ROOT / "agents" / "lead_engineer" / "TOKEN-BUDGET.md"
    agents_md = ROOT / "AGENTS.md"

    if not budget_path.exists():
        warnings.append("agents/lead_engineer/TOKEN-BUDGET.md missing (AGENTS.md §14).")
        return

    text = read_text(budget_path)
    today = _dt.date.today()
    six_months_ago = today - _dt.timedelta(days=183)

    date_matches = re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if not date_matches:
        warnings.append("agents/lead_engineer/TOKEN-BUDGET.md: no YYYY-MM-DD date found.")
        return

    parsed: list[_dt.date] = []
    for y, m, d in date_matches:
        try:
            parsed.append(_dt.date(int(y), int(m), int(d)))
        except ValueError:
            continue
    if not parsed:
        warnings.append("agents/lead_engineer/TOKEN-BUDGET.md: no valid date parsed.")
        return

    newest = max(parsed)
    if newest < six_months_ago:
        warnings.append(
            f"agents/lead_engineer/TOKEN-BUDGET.md: catalog stale "
            f"(newest date {newest.isoformat()} > 6 months old). "
            f"Refresh in next CYCLE RETRO §5 Forward Actions."
        )

    if agents_md.exists():
        agents_text = read_text(agents_md)
        if "TOKEN-BUDGET.md" not in agents_text:
            errors.append(
                "AGENTS.md does not cross-link TOKEN-BUDGET.md "
                "(AGENTS.md §14 protocol broken)."
            )


def check_role_registry(errors: list[str], warnings: list[str]) -> None:
    """TASK-084 agents/roles.yml validation.

    Checks:
      - file exists
      - every role has id, skill_file, aliases (list), required_inputs (list)
      - every skill_file path resolves to an existing file
      - role ids are unique
      - alias collisions across roles are forbidden
    """
    yml = ROOT / "agents" / "roles.yml"
    if not yml.exists():
        return  # registry is optional until TASK-084 lands

    text = yml.read_text(encoding="utf-8")
    # Use the same minimal parser approach as agent_context_packet.py but
    # only collect what we validate. We tolerate format drift outside our keys.
    role_ids: list[str] = []
    role_aliases: dict[str, str] = {}  # alias -> role_id
    skill_files: list[tuple[str, str]] = []  # (role_id, skill_file)
    required_inputs_block: list[tuple[str, list[str]]] = []

    current_role: str | None = None
    current_key: str | None = None
    current_list: list[str] | None = None
    role_skill: dict[str, str] = {}
    role_required: dict[str, list[str]] = {}

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if line.startswith("  - id:"):
            current_role = line.split(":", 1)[1].strip()
            role_ids.append(current_role)
            current_key = None
            current_list = None
            continue
        if current_role is None:
            continue
        if line.startswith("    aliases:"):
            v = line.split(":", 1)[1].strip()
            if v.startswith("[") and v.endswith("]"):
                items = [p.strip() for p in v[1:-1].split(",") if p.strip()]
                for a in items:
                    if a in role_aliases and role_aliases[a] != current_role:
                        errors.append(
                            f"agents/roles.yml: alias '{a}' collides between "
                            f"'{role_aliases[a]}' and '{current_role}'"
                        )
                    role_aliases[a] = current_role
            current_key = None
            continue
        if line.startswith("    skill_file:"):
            v = line.split(":", 1)[1].strip()
            role_skill[current_role] = v
            current_key = None
            continue
        if line.startswith("    required_inputs:"):
            current_key = "required_inputs"
            current_list = []
            role_required[current_role] = current_list
            continue
        if current_key == "required_inputs" and line.startswith("      - "):
            assert current_list is not None
            item = line[8:].strip().strip("'\"")
            current_list.append(item)
            continue
        if current_key == "required_inputs" and not line.startswith("      "):
            current_key = None
            current_list = None

    seen: set[str] = set()
    for rid in role_ids:
        if rid in seen:
            errors.append(f"agents/roles.yml: duplicate role id '{rid}'")
        seen.add(rid)

    for rid, sf in role_skill.items():
        skill_path = ROOT / sf
        if not skill_path.exists():
            errors.append(f"agents/roles.yml: role '{rid}' skill_file '{sf}' not found")

    if role_ids and not role_skill:
        warnings.append("agents/roles.yml: no skill_file values parsed (registry format may have drifted)")


def check_reports(errors: list[str], warnings: list[str]) -> None:
    """TASK-089 BRIEF/PLAN 자동 보관 하네스 검증.

    Validates files under agents/lead_engineer/reports/:
      - frontmatter required fields
      - kind/audience/scale value sets
      - id format and filename ↔ id ↔ kind ↔ date consistency
      - duplicate NNN per (kind, date)
      - INDEX.md ↔ files bidirectional registration
    """
    reports_dir = ROOT / "agents" / "lead_engineer" / "reports"
    index_path = reports_dir / "INDEX.md"
    if not reports_dir.exists():
        return  # nothing to check yet — directory is created when first report is saved

    report_files: list[Path] = []
    for path in sorted(reports_dir.glob("*.md")):
        if path.name in {"README.md", "INDEX.md"}:
            continue
        if path.name.startswith("VIEW-"):
            # auto-generated derivative views (TASK-090) — skip frontmatter checks
            continue
        report_files.append(path)

    seen_ids_per_kind_date: dict[tuple[str, str], set[int]] = {}
    parsed_ids: set[str] = set()

    for path in report_files:
        rel = path.relative_to(ROOT)
        text = read_text(path)
        fm = parse_frontmatter(text)
        if fm is None:
            errors.append(f"{rel}: report file missing YAML frontmatter.")
            continue

        for key in REPORT_REQUIRED_FIELDS:
            if key not in fm or fm[key] in (None, "", []):
                errors.append(f"{rel}: report frontmatter missing key '{key}'.")

        if fm.get("type") != "report":
            errors.append(f"{rel}: report 'type' must be 'report'.")

        report_id = str(fm.get("id", ""))
        m = REPORT_ID_RE.match(report_id)
        if not m:
            errors.append(f"{rel}: report 'id' must match {{BRIEF|PLAN}}-YYYY-MM-DD-NNN (got '{report_id}').")
            continue
        id_kind, id_date, id_seq = m.group(1), m.group(2), int(m.group(3))

        if path.stem != report_id:
            errors.append(f"{rel}: filename stem '{path.stem}' != frontmatter id '{report_id}'.")

        kind_value = str(fm.get("kind", ""))
        if kind_value not in REPORT_KIND_VALUES:
            errors.append(f"{rel}: report 'kind' '{kind_value}' must be in {sorted(REPORT_KIND_VALUES)}.")
        elif kind_value != id_kind:
            errors.append(f"{rel}: report 'kind' '{kind_value}' != id kind '{id_kind}'.")

        date_value = str(fm.get("date", ""))
        if not DATE_ONLY_RE.match(date_value):
            errors.append(f"{rel}: report 'date' must be YYYY-MM-DD.")
        elif date_value != id_date:
            errors.append(f"{rel}: report 'date' '{date_value}' != id date '{id_date}'.")

        audience = str(fm.get("audience", ""))
        if audience not in REPORT_AUDIENCE_VALUES:
            errors.append(f"{rel}: report 'audience' '{audience}' must be in {sorted(REPORT_AUDIENCE_VALUES)}.")

        scale = str(fm.get("scale", ""))
        if scale not in REPORT_SCALE_VALUES:
            errors.append(f"{rel}: report 'scale' '{scale}' must be in {sorted(REPORT_SCALE_VALUES)}.")

        # TASK-124 후속(CYCLE-063): CEO/Owner-facing 보고는 Executive Layer 마커(`Bottom Line:`)
        # 로 시작해야 한다(CLAUDE.md §5.3). cutoff 이후 + audience CEO/Owner/mixed 에만 강제(소급 안 함).
        if (
            DATE_ONLY_RE.match(date_value)
            and date_value >= REPORT_EXECUTIVE_LAYER_FROM
            and audience in REPORT_EXECUTIVE_AUDIENCES
            and not REPORT_BOTTOM_LINE_RE.search(text)
        ):
            errors.append(
                f"{rel}: {audience}-facing {kind_value or 'BRIEF/PLAN'} 에 Executive Layer 마커 누락 — "
                f"본문이 `Bottom Line:` 으로 시작해야 함 (CLAUDE.md §5.3 / REPORTING-FORMAT.md §보고 2-layer)."
            )

        recorded_at = str(fm.get("recorded_at", ""))
        if recorded_at and recorded_at != "unknown" and not is_iso8601(recorded_at):
            errors.append(f"{rel}: report 'recorded_at' must be ISO 8601 or 'unknown' (got '{recorded_at}').")

        for ic_key in ("insights_count", "decisions_count"):
            ic_val = fm.get(ic_key, "")
            try:
                int(str(ic_val))
            except (TypeError, ValueError):
                errors.append(f"{rel}: report '{ic_key}' must be an integer (got '{ic_val}').")

        # duplicate NNN check
        bucket = seen_ids_per_kind_date.setdefault((id_kind, id_date), set())
        if id_seq in bucket:
            errors.append(f"{rel}: duplicate sequence {id_seq:03d} for {id_kind} on {id_date}.")
        bucket.add(id_seq)
        parsed_ids.add(report_id)

        # REPORTING-FORMAT.md §이모지정책 강제: 장식용 이모지 금지, 단순 상태 마커 허용.
        all_hits = REPORT_EMOJI_RE.findall(text)
        disallowed = [g for g in all_hits if g not in REPORT_ALLOWED_STATUS_GLYPHS]
        allowed_count = len(all_hits) - len(disallowed)
        if disallowed:
            first = disallowed[0]
            errors.append(
                f"{rel}: BRIEF/PLAN 은 장식용 이모지 미사용 (REPORTING-FORMAT.md §이모지정책). "
                f"발견: '{first}' (U+{ord(first):04X}). "
                f"O/X/체크박스 같은 단순 상태 마커는 허용되나 장식 이모지는 G/Y/R 텍스트 라벨로."
            )
        elif allowed_count > REPORT_STATUS_GLYPH_WARN_THRESHOLD:
            warnings.append(
                f"{rel}: 상태 마커 글리프 {allowed_count}개 — 남발 주의 "
                f"(REPORTING-FORMAT.md §이모지정책, 권장 <= {REPORT_STATUS_GLYPH_WARN_THRESHOLD})."
            )

    # INDEX.md ↔ files bidirectional registration
    if not index_path.exists():
        if report_files:
            errors.append("agents/lead_engineer/reports/INDEX.md missing — reports/ contains files but INDEX is absent.")
        return

    index_text = read_text(index_path)
    index_ids: set[str] = set()
    for m in re.finditer(r"\[((?:BRIEF|PLAN)-\d{4}-\d{2}-\d{2}-\d{3})\]", index_text):
        index_ids.add(m.group(1))

    for rid in parsed_ids:
        if rid not in index_ids:
            errors.append(f"reports/INDEX.md: missing entry for {rid} (file exists but not indexed).")
    for rid in index_ids:
        if rid not in parsed_ids:
            errors.append(f"reports/INDEX.md: stale entry {rid} (indexed but file missing).")


def check_edge_function_cors(errors: list[str], warnings: list[str]) -> None:
    """COMPOUND(CYCLE-065): Managed database Edge Function 이 브라우저에서 호출되려면 CORS preflight(OPTIONS)
    처리 + Access-Control-Allow-Origin 응답 헤더가 필수다. 누락 시 브라우저가 요청을 차단 →
    클라이언트 FunctionsFetchError → silent fallback 으로 실패가 가려진다(create-user/log-access 2회 재발).
    재발 차단(automated lint): Managed database/functions/*/index.ts 에 Deno.serve 가 있으면 OPTIONS 분기 +
    Access-Control-Allow-Origin 존재를 강제한다."""
    fns_dir = ROOT / "Managed database" / "functions"
    if not fns_dir.exists():
        return
    for index_ts in sorted(fns_dir.glob("*/index.ts")):
        text = read_text(index_ts)
        if "Deno.serve" not in text:
            continue  # 브라우저 호출 진입점이 아니면 대상 아님
        rel = index_ts.relative_to(ROOT).as_posix()
        has_options = re.search(r'method\s*===?\s*["\']OPTIONS["\']', text) is not None
        has_acao = "Access-Control-Allow-Origin" in text
        if not has_options or not has_acao:
            missing = []
            if not has_options:
                missing.append("OPTIONS preflight 분기")
            if not has_acao:
                missing.append("Access-Control-Allow-Origin 헤더")
            errors.append(
                f"{rel}: Edge Function CORS 누락 ({', '.join(missing)}) — 브라우저 invoke 가 "
                f"FunctionsFetchError 로 차단된다. OPTIONS 응답 + 응답 CORS 헤더 추가 (COMPOUND CYCLE-065)."
            )


def check_independent_audit_gate(errors: list[str], warnings: list[str], infos: list[str]) -> None:
    """TASK-063 Independent Audit Gate + TASK-064 self-review/감사·비용 누락 검출.

    적용 대상: TASK-048+ frontmatter 있는 TASK 중 status == 완료
    legacy 면제: frontmatter 에 `audit_exempt` 가 있으면 게이트 도입 전 완료/흡수된
      legacy TASK 로 보고 WARN 대신 INFO 로 가시화만 유지 (AUDIT-GATE.md §legacy cutoff,
      COMPOUND-006). 새 게이트는 도입 이후 완료 TASK 부터만 강제.
    검출:
      - High/Critical 완료에 `## Independent Audit` 섹션 누락 (WARN)
      - Audit 섹션 있으나 필수 필드 누락 (WARN)
      - 판정 값이 {통과/보류/재검토 필요} 외 (ERROR)
      - 보류/재검토 판정인데 해소 조건 없음 (ERROR)
      - 실측 비용 (시간) 또는 (LLM 토큰) 누락 (WARN)
      - 검토자 == Owner (자체 검토 명시 없으면 WARN)

    AGENTS §15.7 단일 세션 환경 예외:
      - 본문에 `single-session env` 문자열이 있으면 self-review WARN 을 통과 처리
        (운영 환경이 단일 LLM 세션이라 별도 세션 검토자 동원 불가능한 케이스).
    """
    HIGH_CRIT = {"Critical", "High"}
    AUDIT_HEADER_RE = re.compile(r"^##\s+Independent Audit\s*$", re.MULTILINE)
    VERDICT_RE = re.compile(r"^판정\s*:\s*(.+?)\s*$", re.MULTILINE)
    RESOLVE_RE = re.compile(r"^해소 조건\s*:\s*(.+?)$", re.MULTILINE)
    ACTUAL_HOURS_RE = re.compile(r"실측 비용 \(시간\)\s*:\s*\S")
    ACTUAL_TOKENS_RE = re.compile(r"실측 비용 \(LLM 토큰\)\s*:\s*\S")
    REVIEWER_RE = re.compile(r"^검토자\s*:\s*(.+?)\s*$", re.MULTILINE)
    OWNER_RE = re.compile(r"^Owner\s*:\s*(.+?)\s*$", re.MULTILINE)

    VALID_VERDICTS = {"통과", "보류", "재검토 필요"}
    # AGENTS §16 협업 의무화 cutoff (도입 시각). 이후 완료 TASK 부터만 협업 흔적 강제.
    COLLAB_MANDATE_CUTOFF = "2026-05-27T15:40"
    # TASK-236: 협업을 WARN→ERROR 로 격상하는 cutoff. 비소급 — 이 시각 이후 완료분만.
    # (소프트 WARN 이 효율압박에 매번 생략된 COMPOUND-034 의 코드 강제. self-review 는
    #  명시 waiver 가 있어야 통과 — substring 루프홀 제거. 판정은 collab_evidence_present.)
    COLLAB_ENFORCE_CUTOFF = "2026-06-05T09:30:00+09:00"
    # TASK-239: High/Critical 완료에 model routing/eval evidence 를 요구한다. 비소급.
    ROUTING_ENFORCE_CUTOFF = "2026-06-06T03:24:19+09:00"

    for path in task_assignment_files():
        text = read_text(path)
        file_task_id = extract_task_id_from_filename(path)
        number = task_number(file_task_id)
        if number is None or number < FRONTMATTER_REQUIRED_TASK_NUMBER:
            continue
        fm = parse_frontmatter(text)
        if fm is None or fm.get("status") != "완료":
            continue
        rel = path.relative_to(ROOT)
        priority = fm.get("priority", "")

        # AUDIT-GATE.md §legacy cutoff: 게이트 도입 전 완료/흡수된 legacy TASK 는
        # audit_exempt frontmatter 로 면제. WARN 대신 INFO 로 가시화만 유지 (COMPOUND-006).
        exempt = fm.get("audit_exempt")
        if exempt:
            infos.append(f"{rel}: audit-gate 면제 (legacy): {exempt}")
            continue

        # 완료 기록 본문 부분만 검사 (frontmatter 이후)
        body_start = text.find("\n---\n", 4)
        body = text[body_start + 5:] if body_start != -1 else text

        # 실측 비용 누락
        if not ACTUAL_HOURS_RE.search(body):
            warnings.append(f"{rel}: 완료 TASK 본문에 '실측 비용 (시간)' 누락")
        if not ACTUAL_TOKENS_RE.search(body):
            warnings.append(f"{rel}: 완료 TASK 본문에 '실측 비용 (LLM 토큰)' 누락")

        # Independent Audit 섹션 — Critical/High 완료시 필수
        has_audit = bool(AUDIT_HEADER_RE.search(body))
        if priority in HIGH_CRIT:
            if not has_audit:
                warnings.append(
                    f"{rel}: priority {priority} 완료 - '## Independent Audit' 섹션 누락 "
                    f"(AUDIT-GATE.md 필수 적용 조건 #1)"
                )
            else:
                # Audit 섹션이 있으면 형식 검증
                verdict_match = VERDICT_RE.search(body)
                if not verdict_match:
                    warnings.append(f"{rel}: Independent Audit 섹션에 '판정' 필드 누락")
                else:
                    verdict_raw = verdict_match.group(1).strip()
                    # 판정 값에서 첫 토큰만 추출 (예: "재검토 필요" or "통과")
                    verdict_normalized = verdict_raw.split("(")[0].split("—")[0].strip()
                    if verdict_normalized not in VALID_VERDICTS:
                        # 판정 값이 여러 옵션을 같이 적은 경우 (예: "통과 / 보류 / 재검토 필요") 허용 — 템플릿 형식
                        if not all(v in verdict_raw for v in VALID_VERDICTS):
                            errors.append(
                                f"{rel}: Independent Audit '판정' 값 '{verdict_normalized}' 가 "
                                f"{sorted(VALID_VERDICTS)} 중 하나가 아님"
                            )
                    # 보류/재검토 판정 시 해소 조건 필수
                    if verdict_normalized in {"보류", "재검토 필요"}:
                        resolve_match = RESOLVE_RE.search(body)
                        if not resolve_match or not resolve_match.group(1).strip().startswith(("(", "—")) is False and (not resolve_match or len(resolve_match.group(1).strip()) < 10):
                            # 해소 조건이 너무 짧거나 placeholder 면 ERROR
                            if not resolve_match or len(resolve_match.group(1).strip()) < 5 or resolve_match.group(1).strip() in {"(보류/재검토 시 필수)", "-"}:
                                errors.append(
                                    f"{rel}: Independent Audit 판정 '{verdict_normalized}' "
                                    f"- '해소 조건' 필수 (AUDIT-GATE.md 판정값)"
                                )

        # self-review 검출: 검토자 == Owner 이고 명시적 self-review 인식 없음
        # §15.7 단일 세션 환경 예외: 본문에 `single-session env` 명시 시 통과
        reviewer_match = REVIEWER_RE.search(body)
        owner_match = OWNER_RE.search(body)
        if reviewer_match and owner_match:
            reviewer = reviewer_match.group(1).strip()
            owner = owner_match.group(1).strip()
            # 검토자 필드가 owner를 정확히 명시하면서 명시적 self-review/단일 세션 표기가 없으면 경고
            if (
                owner in reviewer
                and "self-review" not in body
                and "자체 검토" not in reviewer
                and "single-session env" not in body
                and "(" not in reviewer
            ):
                warnings.append(
                    f"{rel}: 검토자 '{reviewer}' 가 Owner '{owner}' 와 같음 — self-review 명시 또는 외부 검토 권장"
                )

        # AGENTS §16 협업 의무화 (TASK-123) + 코드 강제 (TASK-236, COMPOUND-034).
        # High/Critical 완료 TASK 는 협업 증거가 본문에 있어야 한다. 비소급 cutoff:
        #   - ENFORCE_CUTOFF 이후: 실 협업(이름있는 외부 검토자/council/collab 이벤트) 또는
        #     명시 waiver(사유 동반) 없으면 ERROR. substring 'subagent' 루프홀 제거 —
        #     soft WARN 이 효율압박에 매번 생략된 구조(COMPOUND-034)를 코드로 차단.
        #   - MANDATE..ENFORCE 사이 완료분: 기존 약한 WARN 유지(소급 안 함).
        if priority in HIGH_CRIT:
            completed_at = str(fm.get("completed_at", ""))
            if completed_at and completed_at >= COLLAB_ENFORCE_CUTOFF:
                if not collab_evidence_present(body):
                    errors.append(
                        f"{rel}: priority {priority} 완료에 실 협업 증거도 명시 waiver 도 없음 "
                        f"(TASK-236/§16 — 이름있는 검토자(code-reviewer/skeptic/council) 또는 "
                        f"'협업 waiver(사유)' 필요. self-review 는 명시 waiver 동반)."
                    )
            elif completed_at and completed_at >= COLLAB_MANDATE_CUTOFF:
                has_collab = any(
                    marker in body
                    for marker in ("subagents_used", "subagent", "council", "single-session env")
                )
                if not has_collab:
                    warnings.append(
                        f"{rel}: priority {priority} 완료(§16 cutoff 이후)에 협업 흔적 없음 "
                        f"(AGENTS §16 — subagents_used/council/single-session env 명시 권장)"
                    )
            if completed_at and completed_at >= ROUTING_ENFORCE_CUTOFF:
                if not routing_evidence_present(fm, body):
                    errors.append(
                        f"{rel}: priority {priority} 완료에 라우팅 기록 누락 "
                        f"(TASK-239 — routing_ref/eval_ref 또는 selected_model/policy_model 증거 필요)."
                    )


def check_backlog_fresh(errors: list[str]) -> None:
    """BACKLOG.md(열린 작업 단일 포인터)가 TASK frontmatter 와 동기인지 — 드리프트 차단.

    근거 AUDIT-2026-06-04-002: 단일 repo-canonical 포인터가 항상 최신이어야 어느
    세션/PC/에이전트든 같은 상태에 수렴(중복작업 방지). generate_views 로 재생성한
    내용과 디스크가 (생성 시각 줄 제외) 다르면 stale → ERROR.
    """
    backlog = ROOT / "agents" / "lead_engineer" / "tasks" / "BACKLOG.md"
    if not backlog.exists():
        errors.append("agents/lead_engineer/tasks/BACKLOG.md: 단일 포인터 누락 — `python scripts/generate_views.py` 실행")
        return
    try:
        import importlib.util
        gv_path = Path(__file__).resolve().parent / "generate_views.py"
        spec = importlib.util.spec_from_file_location("_generate_views", gv_path)
        gv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gv)
        expected = gv.view_backlog(gv.load_tasks(), "TS")
    except Exception as exc:  # pragma: no cover - 방어
        errors.append(f"BACKLOG.md 신선도 검사 실패: {exc}")
        return

    def norm(s: str) -> str:  # 생성 시각 줄은 매번 바뀌므로 제외하고 비교
        return "\n".join(ln for ln in s.splitlines() if "생성 시각" not in ln).strip()

    if norm(backlog.read_text(encoding="utf-8")) != norm(expected):
        errors.append(
            "agents/lead_engineer/tasks/BACKLOG.md: TASK frontmatter 와 불일치(stale) — "
            "`python scripts/generate_views.py` 재실행 후 커밋"
        )


# 작업 기록 강제(Owner 지시 2026-06-04): 완료 TASK 는 "프롬프트 요구사항 · 작업 내용 · 작업
# 결과" 를 항상 남긴다. 비소급(COMPOUND-006 정책) — 본 규칙 도입 시각(아래) 이후 완료분만 ERROR.
WORK_RECORD_FORCING_FROM = "2026-06-04T14:25:29+09:00"


def check_work_record_completeness(errors: list[str]) -> None:
    """완료 TASK 는 (1) 프롬프트 요구사항 (2) 작업 내용 (3) 작업 결과 를 항상 기록한다.

    Owner 지시(2026-06-04): "프롬프트에 적힌 요구사항과 작업 내용, 작업 결과는 항상 기록".
    비소급 — completed_at(frontmatter) 가 WORK_RECORD_FORCING_FROM 이상인 완료분만 강제하고,
    completed_at 없는 legacy 완료분과 규칙 도입 이전 완료는 면제한다(소급 안 함).
    """
    for path in task_assignment_files():
        text = read_text(path)
        fm = parse_frontmatter(text)
        if fm is None or fm.get("status") != "완료":
            continue
        completed = str(fm.get("completed_at") or "").strip()
        if not completed or completed < WORK_RECORD_FORCING_FROM:
            continue
        rel = path.relative_to(ROOT)
        # (1) 프롬프트 요구사항 — 무엇을·왜 요청했는가가 기록돼야 한다
        if not (
            re.search(r"(?m)^현재 요청:\s*\S", text)
            or "## 요구사항" in text
            or re.search(r"(?m)^요청자:\s*\S", text)
        ):
            errors.append(f"{rel}: 완료 TASK 에 프롬프트 요구사항 기록 누락 ('현재 요청:'/'요청자:'/'## 요구사항').")
        # (2) 작업 내용 — 실제 수행한 것
        if "## 완료 내용" not in text and "## 작업 내용" not in text:
            errors.append(f"{rel}: 완료 TASK 에 작업 내용 기록 누락 ('## 완료 내용').")
        # (3) 작업 결과 — 산출/검증 결과
        if not (
            re.search(r"(?m)^결과:\s*\S", text)
            or "## 결과" in text
            or "## 검증" in text
        ):
            errors.append(f"{rel}: 완료 TASK 에 작업 결과 기록 누락 ('결과:'/'## 검증'/'## 결과').")


def check_task_schema(errors: list[str], warnings: list[str]) -> None:
    """TASK frontmatter 를 schemas/task.schema.json 계약으로 검증 (TASK-231, 구조화 ①).

    validate_task_schema 를 lazy import(순환 회피) 해 위반을 errors 로 승격 → PR CI 차단.
    """
    try:
        import validate_task_schema as vts
        results = vts.validate_all()
    except Exception as exc:  # 도구 자체 실패는 warning(검증 자체를 막지 않음)
        warnings.append(f"task schema 검증 생략(로드 실패): {exc}")
        return
    for name, errs in results.items():
        for err in errs:
            errors.append(f"agents/lead_engineer/tasks/{name}: task.schema.json 위반 — {err}")


def main() -> int:
    # Windows 콘솔에서 UTF-8 출력 보장 (em-dash 등 non-cp949 문자 안전)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    check_stale_cycle_refs(warnings)
    check_tasks(errors, warnings)
    check_task_registry(errors, warnings)
    check_backlog_fresh(errors)
    check_status_doc(errors)
    check_reviews(errors, warnings)
    check_cycle_termination_conditions(errors, warnings)
    check_cycle_referenced_compounds(errors, warnings)
    check_cycle_kedb_search(errors, warnings)
    check_compound_format_v2(errors, warnings)
    check_review_referenced_compounds(errors, warnings)
    check_review_collaboration(errors, warnings)
    check_compound_recurrence_escalation(errors, warnings)
    check_retro_forward_actions(errors, warnings)
    check_compound_log(errors, warnings)
    check_audit_log(errors, warnings)
    check_task_frontmatter(errors, warnings)
    check_task_schema(errors, warnings)
    check_meeting_frontmatter(errors, warnings)
    check_role_registry(errors, warnings)
    check_reports(errors, warnings)
    check_edge_function_cors(errors, warnings)
    check_handoff_sections(errors, warnings)
    check_bootstrap_sync(errors, warnings)
    check_token_budget(errors, warnings)
    check_retros(errors, warnings)
    check_independent_audit_gate(errors, warnings, infos)
    check_work_record_completeness(errors)
    check_markdown_links(warnings)

    latest = latest_cycle_number()
    print("Agent docs consistency check")
    if latest is not None:
        print(f"Latest cycle: CYCLE-{latest:03d}")

    for info in infos:
        print(f"INFO: {info}")
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")

    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK: 0 error(s), {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
