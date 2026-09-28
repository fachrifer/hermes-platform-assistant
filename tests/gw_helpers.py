from __future__ import annotations

from office_gateway.config import GatewayConfig
from office_gateway.docker_ops import DockerOps

TOKENS = {
    "supervisor": "tok-sup",
    "lab-host": "tok-lab",
    "ingress": "tok-ing",
    "llm": "tok-llm",
    "cluster-gpu": "tok-gpu",
    "vector": "tok-vec",
    "obs": "tok-obs",
    "approver": "tok-app",
}


def make_config(tmp_path, **overrides) -> GatewayConfig:
    base = {
        "tokens": dict(TOKENS),
        "service_urls": {"gateway": "http://office-gateway:8080/health"},
        "db_path": str(tmp_path / "gw.db"),
        "console_url": "https://console.test",
    }
    base.update(overrides)
    return GatewayConfig(**base)


def auth(role: str, approver: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {TOKENS[role]}"}
    if approver is not None:
        headers["X-Approver"] = approver
    return headers


def container(name: str, status: str = "running", service: str = "", health=None) -> dict:
    return {
        "name": name,
        "status": status,
        "health": health,
        "image": f"img/{name}:1",
        "compose_service": service,
        "ports": [],
        "networks": [],
        "env_names": ["PATH", "SECRET_TOKEN"],
        "restart_count": 0,
        "started_at": "2026-09-24T00:00:00Z",
    }


class FakeDockerOps(DockerOps):
    def __init__(self, containers=None, logs=None, fail_restart: bool = False):
        super().__init__(client=object())
        self._containers = containers if containers is not None else [
            container("aiplatform-api"),
            container("office-office-edge-1", service="office-edge"),
            container("broken-worker", status="exited"),
        ]
        self._logs = logs or {}
        self._fail_restart = fail_restart
        self.restarted: list[str] = []
        self.execs: list[dict] = []
        self.exec_handler = None

    def exec_run(self, name: str, cmd: list[str], *, user: str = "") -> dict:
        self.execs.append({"name": name, "cmd": list(cmd), "user": user})
        if self.exec_handler is not None:
            return self.exec_handler(name, cmd, user)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    def list_containers(self, all: bool = True) -> dict:
        rows = [dict(c) for c in self._containers]
        running = sum(1 for c in rows if c["status"] == "running")
        return {"containers": rows, "running": running, "total": len(rows), "truncated": False}

    def inspect(self, name: str) -> dict:
        for c in self._containers:
            if c["name"] == name:
                return dict(c)
        raise ValueError(f"container not found: {name}")

    def logs(self, name: str, tail: int = 80) -> dict:
        self.inspect(name)
        lines = list(self._logs.get(name, []))[-tail:]
        return {"name": name, "tail": len(lines), "lines": lines}

    def restart(self, name: str) -> None:
        if self._fail_restart:
            raise RuntimeError("docker down")
        self.inspect(name)
        self.restarted.append(name)
