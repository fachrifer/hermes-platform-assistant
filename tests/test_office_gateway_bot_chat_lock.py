import json

from gw_helpers import auth
from office_gateway.bot_chat_lock import holder_pids, held_leases
from test_office_gateway_approvals import _client

BOT = "20260928_110712_20e357"
HALO = "20260928_110231_7115ab"
TUI = "/opt/hermes/.venv/bin/python3 -m tui_gateway.entry"
DASHBOARD = "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes dashboard --port 9119"


def _inspect(session_ids, leases, cmdlines, code=0, clear_code=0, remaining=0):
    body = json.dumps({"session_ids": session_ids, "leases": leases, "cmdlines": cmdlines})

    def handler(name, cmd, user):
        if cmd[:2] == ["/opt/hermes/.venv/bin/python3", "-c"]:
            assert user == "hermes"
            assert name == "office-hermes-agent-1"
            if "flock" in cmd[2]:
                return {
                    "exit_code": clear_code,
                    "stdout": json.dumps({"removed": 1, "remaining": remaining}),
                    "stderr": "",
                }
            return {"exit_code": code, "stdout": body, "stderr": ""}
        assert cmd[0] == "kill" and cmd[1] == "-TERM" and user == ""
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    return handler


def _lease(session, surface, pid, lease_id):
    return {"session_id": session, "surface": surface, "pid": pid, "lease_id": lease_id}


def _clears(docker):
    return [call["cmd"] for call in docker.execs if len(call["cmd"]) > 2 and "flock" in call["cmd"][2]]

def test_holder_pids_keeps_only_the_bot_chat_tui():
    leases = [
        {"session_id": BOT, "surface": "tui", "pid": 409},
        {"session_id": HALO, "surface": "tui", "pid": 1788},
        {"session_id": BOT, "surface": "cli", "pid": 50},
        {"session_id": BOT, "surface": "tui", "pid": 144},
    ]
    cmdlines = {409: TUI, 1788: TUI, 50: "hermes chat", 144: DASHBOARD}
    assert holder_pids({BOT}, leases, cmdlines) == [409]


def test_held_leases_keeps_every_holder_of_the_bot_chat_session():
    leases = [
        _lease(BOT, "tui", 409, "a"),
        _lease(HALO, "tui", 1788, "b"),
        _lease(BOT, "desktop", 144, "c"),
        {"session_id": BOT, "surface": "desktop", "pid": 144},
    ]
    assert [entry["lease_id"] for entry in held_leases({BOT}, leases)] == ["a", "c"]


def test_release_stops_the_bot_chat_tui_and_clears_only_its_lease(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT],
        [_lease(BOT, "tui", 409, "lease-bot"), _lease(HALO, "tui", 1788, "lease-halo")],
        {"409": TUI, "1788": TUI},
    )
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 200
    assert response.json() == {
        "released": True,
        "reason": "released",
        "session_ids": [BOT],
        "stopped": [409],
        "cleared": [{"session_id": BOT, "surface": "tui", "pid": 409}],
    }
    assert ["kill", "-TERM", "409"] in [call["cmd"] for call in docker.execs]
    assert ["kill", "-TERM", "1788"] not in [call["cmd"] for call in docker.execs]
    (clear,) = _clears(docker)
    assert json.loads(clear[3]) == ["lease-bot"] and json.loads(clear[4]) == [BOT]


def test_release_frees_a_desktop_lease_without_killing_the_dashboard(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT],
        [_lease(BOT, "desktop", 144, "lease-desktop")],
        {"144": DASHBOARD},
    )
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 200
    assert response.json() == {
        "released": True,
        "reason": "released",
        "session_ids": [BOT],
        "stopped": [],
        "cleared": [{"session_id": BOT, "surface": "desktop", "pid": 144}],
    }
    assert all(call["cmd"][0] != "kill" for call in docker.execs)
    (clear,) = _clears(docker)
    assert json.loads(clear[3]) == ["lease-desktop"]


def test_release_frees_a_stale_lease_whose_process_is_gone(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([BOT], [_lease(BOT, "tui", 5751, "lease-dead")], {})
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.json()["released"] is True and response.json()["stopped"] == []
    assert all(call["cmd"][0] != "kill" for call in docker.execs)
    assert len(_clears(docker)) == 1


def test_release_fails_when_the_lease_is_still_there_after_clearing(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT], [_lease(BOT, "desktop", 144, "lease-desktop")], {"144": DASHBOARD}, remaining=1
    )
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 502


def test_release_fails_closed_when_the_registry_cannot_be_written(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT], [_lease(BOT, "desktop", 144, "lease-desktop")], {"144": DASHBOARD}, clear_code=1
    )
    response = client.post("/v1/approvals/bot-chat/release", headers=auth("approver", approver="timai"))
    assert response.status_code == 502


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


def test_bot_chat_info_gives_the_id_to_open_and_who_holds_it(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect(
        [BOT, "child-tip"],
        [_lease(BOT, "desktop", 144, "lease-desktop"), _lease(HALO, "tui", 1788, "other")],
        {"144": DASHBOARD},
    )
    response = client.get("/v1/approvals/bot-chat", headers=auth("approver"))
    assert response.status_code == 200
    assert response.json() == {
        "session_id": BOT,
        "held": True,
        "holders": [{"surface": "desktop", "pid": 144}],
    }
    assert docker.execs and all(call["cmd"][0] != "kill" for call in docker.execs)
    assert _clears(docker) == []


def test_bot_chat_info_when_free_or_missing(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([BOT], [], {})
    assert client.get("/v1/approvals/bot-chat", headers=auth("approver")).json() == {
        "session_id": BOT,
        "held": False,
        "holders": [],
    }
    docker.exec_handler = _inspect([], [], {})
    assert client.get("/v1/approvals/bot-chat", headers=auth("approver")).json() == {
        "session_id": None,
        "held": False,
        "holders": [],
    }


def test_bot_chat_info_is_for_approvers_only_and_fails_closed(tmp_path):
    client, docker, _ = _client(tmp_path)
    assert client.get("/v1/approvals/bot-chat", headers=auth("supervisor")).status_code == 403
    assert docker.execs == []
    docker.exec_handler = _inspect([BOT], [], {}, code=1)
    assert client.get("/v1/approvals/bot-chat", headers=auth("approver")).status_code == 502


def test_open_redirects_to_the_dashboard_chat_by_id(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([BOT], [], {})
    response = client.get("/v1/approvals/bot-chat/open", headers=auth("approver"), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"/dash/chat?resume={BOT}"
    assert all(call["cmd"][0] != "kill" for call in docker.execs) and _clears(docker) == []


def test_open_reports_a_missing_session_and_is_approver_only(tmp_path):
    client, docker, _ = _client(tmp_path)
    docker.exec_handler = _inspect([], [], {})
    missing = client.get("/v1/approvals/bot-chat/open", headers=auth("approver"), follow_redirects=False)
    assert missing.status_code == 404
    docker.execs.clear()
    denied = client.get("/v1/approvals/bot-chat/open", headers=auth("supervisor"), follow_redirects=False)
    assert denied.status_code == 403 and docker.execs == []
