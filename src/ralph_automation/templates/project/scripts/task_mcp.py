#!/usr/bin/env python3
"""task_mcp — 구조화 task 의 최소 MCP 서버 (TASK-233, 구조화 ③ · MCP 부분).

stdio JSON-RPC 2.0(MCP)을 **의존성 없이** 핵심만 구현(initialize/tools/list/tools/call) —
`mcp` 패키지 비의존(repo 정책). 에이전트가 파싱·파일편집 없이 task 를 read/update 하는
표면. 도구는 task_api 에 위임: task_get / task_query / task_set_status.

MCP 클라이언트(예: Claude Code)가 spawn: `python scripts/task_mcp.py` (stdio).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import task_api

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "tag-task-api", "version": "1.0.0"}

TOOLS = [
    {"name": "task_get", "description": "ID로 단일 task 조회",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "task_query", "description": "status/owner/priority/tag 필터로 task 목록 조회",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string"}, "owner": {"type": "string"},
         "priority": {"type": "string"}, "tag": {"type": "string"}}}},
    {"name": "task_set_status", "description": "task status 안전 갱신(frontmatter+body+INDEX 정합)",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"},
         "status": {"type": "string", "enum": task_api.VALID_STATUS}}, "required": ["id", "status"]}},
]


def _tool_call(name: str, args: dict):
    if name == "task_get":
        return task_api.get(args.get("id"))
    if name == "task_query":
        return task_api.query(status=args.get("status"), owner=args.get("owner"),
                              priority=args.get("priority"), tag=args.get("tag"))
    if name == "task_set_status":
        return task_api.update_status(args.get("id"), args.get("status"))
    raise ValueError(f"unknown tool: {name}")


def handle_request(req: dict) -> dict | None:
    """JSON-RPC 요청 → 응답 dict. notification(id 없음)·initialized 면 None."""
    mid = req.get("id")
    method = req.get("method")
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                      "serverInfo": SERVER_INFO}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params", {})
            out = _tool_call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
        elif method in ("notifications/initialized", "initialized"):
            return None  # notification — 응답 없음
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle_request(req)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
