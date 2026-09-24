import json

from fastapi.testclient import TestClient

from gw_helpers import FakeDockerOps, auth, make_config
from office_gateway.app import create_app


def _client(tmp_path):
    return TestClient(create_app(make_config(tmp_path), docker_ops=FakeDockerOps()))


def _rpc(client, role, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    headers = {**auth(role), "Accept": "application/json, text/event-stream"}
    return client.post("/mcp", headers=headers, json=body)


def test_initialize_echoes_supported_version(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "lab-host", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    result = r.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    r = _rpc(client, "lab-host", "initialize", {"protocolVersion": "1999-01-01"})
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"


def test_notifications_get_202(tmp_path):
    r = _rpc(_client(tmp_path), "lab-host", "notifications/initialized", id_=None)
    assert r.status_code == 202 and r.content == b""


def test_tools_list_is_role_filtered(tmp_path):
    client = _client(tmp_path)
    sup = {t["name"] for t in _rpc(client, "supervisor", "tools/list").json()["result"]["tools"]}
    assert sup == {"fleet_status"}
    lab = _rpc(client, "lab-host", "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in lab}
    assert "propose_restart" in names and "fleet_status" not in names
    tool = next(t for t in lab if t["name"] == "tail_logs")
    assert tool["inputSchema"]["properties"]["lines"]["maximum"] == 100


def test_tools_call_returns_envelope_text(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "lab-host", "tools/call", {"name": "list_containers", "arguments": {}})
    result = r.json()["result"]
    envelope = json.loads(result["content"][0]["text"])
    assert result["isError"] is False and envelope["data"]["total"] == 3


def test_tools_call_forbidden_tool_is_error_envelope(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "supervisor", "tools/call", {"name": "propose_restart", "arguments": {"container": "x", "reason": "y"}})
    result = r.json()["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["category"] == "forbidden"


def test_auth_and_methods(tmp_path):
    client = _client(tmp_path)
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert _rpc(client, "approver", "tools/list").status_code == 403
    assert client.get("/mcp", headers=auth("lab-host")).status_code == 405
    assert _rpc(client, "lab-host", "ping").json()["result"] == {}
    assert _rpc(client, "lab-host", "resources/list").json()["error"]["code"] == -32601
    bad = client.post("/mcp", headers={**auth("lab-host"), "Content-Type": "application/json"}, content=b"{")
    assert bad.json()["error"]["code"] == -32700
