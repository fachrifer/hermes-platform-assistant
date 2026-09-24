from office_gateway.docker_ops import strip_inspect, DockerOps


def test_strip_inspect_keeps_env_names_only_and_drops_mounts():
    raw = {
        "Name": "/aiplatform-dashboard",
        "State": {"Status": "running", "Health": {"Status": "unhealthy"}, "StartedAt": "2026-09-24T01:00:00Z"},
        "Config": {"Image": "dashboard:latest", "Env": ["SECRET=hunter2", "PATH=/usr/bin"]},
        "Mounts": [{"Source": "/var/run/secrets", "Destination": "/secrets"}],
        "RestartCount": 3,
    }
    out = strip_inspect(raw)
    assert out["name"] == "aiplatform-dashboard"
    assert out["status"] == "running"
    assert out["health"] == "unhealthy"
    assert out["env_names"] == ["PATH", "SECRET"]
    assert "hunter2" not in str(out) and "/usr/bin" not in str(out)
    assert out["restart_count"] == 3
    assert out["started_at"] == "2026-09-24T01:00:00Z"
    assert "Mounts" not in out and "/var/run/secrets" not in str(out)


def test_compact_listing_counts_and_names():
    from office_gateway.docker_ops import compact_listing

    rows = [
        {"name": "a", "status": "running"},
        {"name": "b", "status": "exited"},
        {"name": "c", "status": "running", "health": "unhealthy"},
    ]
    out = compact_listing(rows)
    assert (out["running"], out["total"], out["exited"]) == (2, 3, 1)
    assert out["not_running"] == ["b (exited)"]
    assert out["unhealthy"] == ["c"]
    assert out["names"] == ["a", "b", "c"]
    assert "heal_candidates" not in out


def test_restart_unknown_name_rejected(tmp_path):
    calls = {"restart": False}

    class FakeContainer:
        def restart(self):
            calls["restart"] = True

    class FakeContainers:
        def get(self, name):
            raise KeyError(name)

    class Fake:
        containers = FakeContainers()

    ops = DockerOps(client=Fake())
    try:
        ops.restart("nope")
        raise AssertionError("expected error")
    except ValueError as exc:
        assert "unknown" in str(exc).lower() or "not found" in str(exc).lower()
    assert not calls["restart"]


def test_inspect_client_failure_becomes_container_not_found():
    class BrokenOps(DockerOps):
        def _client_or_docker(self):
            raise ConnectionError("/var/run/docker.sock")

    ops = BrokenOps()
    try:
        ops.inspect("missing")
        raise AssertionError("expected error")
    except ValueError as exc:
        assert str(exc) == "container not found: missing"
        assert "/var/run/docker.sock" not in str(exc)
