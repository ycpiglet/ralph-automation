"""TASK-233 — 최소 MCP 서버 테스트(dispatch 로직 + serve 스모크)."""
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_tmcp", ROOT / "scripts" / "task_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mcp = _load()


def _req(mid, method, params=None):
    r = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        r["params"] = params
    return r


def test_initialize():
    r = mcp.handle_request(_req(1, "initialize"))
    assert r["result"]["serverInfo"]["name"] == "tag-task-api"
    assert r["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_tools_list():
    r = mcp.handle_request(_req(2, "tools/list"))
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {"task_get", "task_query", "task_set_status"}


def test_tools_call_get():
    r = mcp.handle_request(_req(3, "tools/call", {"name": "task_get", "arguments": {"id": "TASK-231"}}))
    txt = r["result"]["content"][0]["text"]
    assert "TASK-231" in txt


def test_tools_call_query_tag():
    r = mcp.handle_request(_req(4, "tools/call", {"name": "task_query", "arguments": {"tag": "task-model"}}))
    data = json.loads(r["result"]["content"][0]["text"])
    assert isinstance(data, list) and len(data) >= 4


def test_unknown_method():
    r = mcp.handle_request(_req(5, "nope/nope"))
    assert r["error"]["code"] == -32601


def test_unknown_tool_errors():
    r = mcp.handle_request(_req(6, "tools/call", {"name": "bogus", "arguments": {}}))
    assert "error" in r


def test_notification_returns_none():
    assert mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_serve_smoke():
    # read-only 요청 2건을 stdio 로 흘려보내 응답 라인 수 확인
    inp = io.StringIO(
        json.dumps(_req(1, "initialize")) + "\n" +
        json.dumps(_req(2, "tools/list")) + "\n")
    out = io.StringIO()
    mcp.serve(stdin=inp, stdout=out)
    lines = [l for l in out.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2
    assert all("result" in json.loads(l) for l in lines)
