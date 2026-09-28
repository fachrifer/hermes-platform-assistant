import json

from gw_helpers import auth
from office_gateway.bot_chat_lock import holder_pids
from test_office_gateway_approvals import _client

BOT = "20260928_110712_20e357"
HALO = "20260928_110231_7115ab"
TUI = "/opt/hermes/.venv/bin/python3 -m tui_gateway.entry"
DASHBOARD = "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes dashboard --port 9119"


def _inspect(session_ids, leases, cmdlines, code=0):
    body = json.dumps({"session_ids": session_ids, "leases": leases, "cmdlines": cmdlines})

    def handler(name, cmd, user):
        if cmd[:2] == ["/opt/hermes/.venv/bin/python3", "-c"]:
            assert user == "hermes"
            assert name == "office-hermes-agent-1"
            return {"exit_code": code, "stdout": body, "stderr": ""}
        assert cmd[0] == "kill" and cmd[1] == "-TERM" and user == ""
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    return handler


def test_holder_pids_keeps_only_the_bot_chat_tui():
    leases = [
        {"session_id": BOT, "surface": "tui", "pid": 409},
        {"session_id": HALO, "surface": "tui", "pid": 1788},
        {"session_id": BOT, "surface": "cli", "pid": 50},
        {"session_id": BOT, "surface": "tui", "pid": 144},
    ]
    cmdlines = {409: TUI, 1788: TUI, 50: "hermes chat", 144: DASHBOARD}
    assert holder_pids({BOT}, leases, cmdlines) == [409]


def test_release_stops_the_bot_chat_tui_only(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT],
        [
            {"session_id": BOT, "surface": "tui", "pid": 409},
            {"session_id": HALO, "surface": "tui", "pid": 1788},
        ],
        {"409": TUI, "1788": TUI},
    )
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 200
    assert response.json() == {
        "released": True,
        "reason": "released",
        "session_ids": [BOT],
        "stopped": [409],
    }
    assert ["kill", "-TERM", "409"] in [call["cmd"] for call in docker.execs]
    assert ["kill", "-TERM", "1788"] not in [call["cmd"] for call in docker.execs]


def test_release_reports_when_nothing_holds_the_session(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([BOT], [], {})
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 200
    assert response.json()["released"] is False
    assert response.json()["reason"] == "not_held"
    assert all(call["cmd"][0] != "kill" for call in docker.execs)


def test_release_reports_a_missing_session(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([], [], {})
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.json()["reason"] == "no_session"
    assert all(call["cmd"][0] != "kill" for call in docker.execs)


def test_agents_cannot_release_the_lock(tmp_path):
    client, docker, _ = _client(tmp_path)
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("supervisor", approver="timai"))
    assert response.status_code == 403
    assert docker.execs == []


def test_release_requires_the_approver_header(tmp_path):
    client, docker, _ = _client(tmp_path)
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver"))
    assert response.status_code == 400
    assert docker.execs == []


def test_release_fails_closed_when_athena_cannot_be_inspected(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([BOT], [{"session_id": BOT, "surface": "tui", "pid": 409}], {"409": TUI}, code=1)
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 502
    assert all(call["cmd"][0] != "kill" for call in docker.execs)
