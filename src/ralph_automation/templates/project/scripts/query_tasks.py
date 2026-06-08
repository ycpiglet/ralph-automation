"""
TASK 필터링 CLI — YAML frontmatter 기반.

사용 예:
  python scripts/query_tasks.py
  python scripts/query_tasks.py --status 대기
  python scripts/query_tasks.py --priority Critical
  python scripts/query_tasks.py --owner backend --tag security
  python scripts/query_tasks.py --difficulty 낮 --format json

frontmatter가 없는 legacy TASK(주로 TASK-001 ~ TASK-047)는 결과에서 제외된다.
신규 TASK(TASK-048+)는 모두 frontmatter를 갖는 것이 표준이며 `check_agent_docs.py`가 강제한다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "agents" / "lead_engineer" / "tasks"
INDEX_PATH = TASKS_DIR / "INDEX.md"
ALLOWED_STATES = {"대기", "진행 중", "완료", "보류"}


def parse_frontmatter(text: str) -> dict | None:
    """check_agent_docs.py와 동일한 최소 YAML frontmatter 파서."""
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


def load_tasks() -> list[tuple[Path, dict]]:
    results: list[tuple[Path, dict]] = []
    for path in sorted(TASKS_DIR.glob("TASK-*.md")):
        if path.name.endswith("-result.md"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm is None:
            continue
        results.append((path, fm))
    return results


def match_filter(fm: dict, args: argparse.Namespace) -> bool:
    if args.status and fm.get("status") != args.status:
        return False
    if args.priority and fm.get("priority", "").lower() != args.priority.lower():
        return False
    if args.difficulty and fm.get("difficulty") != args.difficulty:
        return False
    if args.owner:
        owner = (fm.get("owner") or "").lower()
        if args.owner.lower() not in owner:
            return False
    if args.tag:
        tags = [t.lower() for t in fm.get("tags") or []]
        if args.tag.lower() not in tags:
            return False
    if args.trigger_meeting and fm.get("trigger_meeting") != args.trigger_meeting:
        return False
    return True


def render_table(rows: list[tuple[Path, dict]]) -> str:
    if not rows:
        return "No TASKs matched.\n"
    headers = ("ID", "STATUS", "PRIORITY", "DIFF", "EST(ph)", "OWNER", "TAGS")
    widths = [10, 8, 9, 5, 7, 30, 35]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "-" * len(line)]
    for _, fm in rows:
        tags = ",".join((fm.get("tags") or [])[:5])
        if len(tags) > widths[6]:
            tags = tags[: widths[6] - 1] + "…"
        owner = (fm.get("owner") or "-")[: widths[5]]
        cells = (
            (fm.get("id") or "-")[: widths[0]],
            (fm.get("status") or "-")[: widths[1]],
            (fm.get("priority") or "-")[: widths[2]],
            (fm.get("difficulty") or "-")[: widths[3]],
            str(fm.get("est_hours") or "-")[: widths[4]],
            owner,
            tags,
        )
        out.append("  ".join(c.ljust(w) for c, w in zip(cells, widths)))
    out.append("")
    out.append(f"{len(rows)} TASK(s) matched.")
    return "\n".join(out) + "\n"


def render_json(rows: list[tuple[Path, dict]]) -> str:
    payload = []
    for path, fm in rows:
        payload.append({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            **fm,
        })
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ---------- writer (task CRUD — TASK-224) ----------
# 단일출처 유지: TASK 파일 frontmatter + body 상태 + INDEX.md 행을 동기화하고
# generate_views 를 트리거한다. 별도 store 를 만들지 않는다(COMPOUND-032).
# check_agent_docs 는 frontmatter status == body 상태 == INDEX status 셋 다 일치를 강제한다.

def rewrite_task_status(text: str, new_status: str) -> str:
    """TASK 파일 본문의 frontmatter `status:` 와 body `상태:` 를 모두 new_status 로."""
    text = re.sub(r"(?m)^(status:\s*).+$", rf"\g<1>{new_status}", text, count=1)
    text = re.sub(r"(?m)^(상태:\s*).+$", rf"\g<1>{new_status}", text, count=1)
    return text


def rewrite_index_status(index_text: str, task_id: str, new_status: str) -> str:
    """INDEX.md 의 task_id 행 상태 셀(2번째)을 new_status 로 교체."""
    def repl(m: re.Match) -> str:
        cells = m.group(0).split("|")
        # cells: ['', ' [TASK-NNN](..) ', ' 상태 ', ' Owner ', ...] (leading '' from leading |)
        if len(cells) >= 3:
            cells[2] = f" {new_status} "
        return "|".join(cells)
    pattern = re.compile(rf"(?m)^\|\s*\[{re.escape(task_id)}\][^\n]*$")
    return pattern.sub(repl, index_text, count=1)


def _find_task_file(task_id: str) -> Path | None:
    matches = sorted(TASKS_DIR.glob(f"{task_id}-*.md"))
    matches = [p for p in matches if not p.name.endswith("-result.md")]
    return matches[0] if matches else None


def _regenerate_views() -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_views.py")],
                   cwd=ROOT, capture_output=True, text=True)


def set_status(task_id: str, new_status: str) -> tuple[bool, str]:
    if new_status not in ALLOWED_STATES:
        return False, f"invalid status '{new_status}'. Use one of {sorted(ALLOWED_STATES)}."
    path = _find_task_file(task_id)
    if path is None:
        return False, f"TASK file for {task_id} not found."
    path.write_text(rewrite_task_status(path.read_text(encoding="utf-8"), new_status), encoding="utf-8")
    if INDEX_PATH.exists():
        INDEX_PATH.write_text(
            rewrite_index_status(INDEX_PATH.read_text(encoding="utf-8"), task_id, new_status),
            encoding="utf-8",
        )
    _regenerate_views()
    return True, f"{task_id} → {new_status} (frontmatter+body+INDEX sync, views regenerated)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TASK frontmatter filter + writer")
    parser.add_argument("--status", help="대기 / 진행 중 / 완료 / 보류")
    parser.add_argument("--priority", help="Critical / High / Medium / Low")
    parser.add_argument("--difficulty", help="낮 / 중 / 중-상 / 상")
    parser.add_argument("--owner", help="substring match (case-insensitive)")
    parser.add_argument("--tag", help="exact tag match (case-insensitive)")
    parser.add_argument("--trigger-meeting", dest="trigger_meeting", help="MEETING ID exact match")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--set-status", metavar="TASK-NNN",
                        help="writer: 지정 TASK 의 상태를 --to 값으로 변경(frontmatter+body+INDEX 동기 + views 재생성)")
    parser.add_argument("--to", help="--set-status 와 함께: 새 상태(대기/진행 중/완료/보류)")
    args = parser.parse_args(argv)

    if args.set_status:
        if not args.to:
            sys.stderr.write("--set-status 에는 --to <상태> 가 필요합니다.\n")
            return 2
        ok, msg = set_status(args.set_status, args.to)
        sys.stdout.write(msg + "\n")
        return 0 if ok else 1

    all_tasks = load_tasks()
    matched = [(p, fm) for p, fm in all_tasks if match_filter(fm, args)]

    if args.format == "json":
        sys.stdout.write(render_json(matched))
    else:
        sys.stdout.write(render_table(matched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
