from office_gateway.docker_ops import strip_inspect, DockerOps


def test_strip_inspect_drops_env_and_mounts():
    raw = {
        "Name": "/aiplatform-dashboard",
        "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
        "Config": {"Image": "dashboard:latest", "Env": ["SECRET=1"]},
        "Mounts": [{"Source": "/var/run/secrets", "Destination": "/secrets"}],
    }
    out = strip_inspect(raw)
    assert out["name"] == "aiplatform-dashboard"
    assert out["status"] == "running"
    assert out["health"] == "unhealthy"
    assert "Env" not in str(out)
    assert "SECRET" not in str(out)
    assert "Mounts" not in out


def test_restart_unknown_name_rejected(tmp_path):
    class Fake:
        def inspect_container(self, name):
            raise KeyError(name)

        def restart(self, name):
            raise AssertionError("must not restart")

    ops = DockerOps(client=Fake())
    try:
        ops.restart("nope")
        raise AssertionError("expected error")
    except ValueError as exc:
        assert "unknown" in str(exc).lower() or "not found" in str(exc).lower()
