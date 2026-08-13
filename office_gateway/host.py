"""Lab VM snapshot: Docker processes plus host CPU, memory, and disk."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

DockerRequest = Callable[[str], tuple[int, Any]]
DiskUsage = Callable[[str], tuple[int, int, int]]


def _container_name(raw: dict) -> str:
    names = raw.get("Names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(raw.get("Id", "unknown"))[:12]


class HostVmCollector:
    """Read local Docker + /proc + disk. Never include socket or filesystem paths."""

    def __init__(
        self,
        *,
        docker_sock: str = "/var/run/docker.sock",
        host_proc: str = "/host/proc",
        host_root: str = "/host/root",
        cpu_critical: float = 90.0,
        memory_critical: float = 90.0,
        disk_critical: float = 90.0,
        docker_request: DockerRequest | None = None,
        disk_usage: DiskUsage | None = None,
    ) -> None:
        self.docker_sock = docker_sock
        self.host_proc = host_proc
        self.host_root = host_root
        self.cpu_critical = cpu_critical
        self.memory_critical = memory_critical
        self.disk_critical = disk_critical
        self._docker_request = docker_request or self._docker_http
        self._disk_usage = disk_usage or self._statvfs

    def snapshot(self) -> dict:
        docker = self._docker()
        cpu = self._cpu()
        memory = self._memory()
        disk = self._disk()
        critical = docker["daemon"] != "ok"
        for part, limit in (
            (cpu, self.cpu_critical),
            (memory, self.memory_critical),
            (disk, self.disk_critical),
        ):
            percent = part.get("percent")
            if isinstance(percent, (int, float)) and percent >= limit:
                critical = True
        return {
            "name": "lab-vm",
            "status": "critical" if critical else "ok",
            "docker": docker,
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
        }

    def _docker(self) -> dict:
        try:
            ping_status, _ = self._docker_request("/_ping")
        except (httpx.HTTPError, OSError, ValueError):
            return {"daemon": "critical", "running": 0, "exited": 0, "containers": []}
        if ping_status >= 400:
            return {"daemon": "critical", "running": 0, "exited": 0, "containers": []}
        try:
            list_status, payload = self._docker_request("/containers/json")
        except (httpx.HTTPError, OSError, ValueError):
            return {"daemon": "ok", "running": 0, "exited": 0, "containers": []}
        if list_status >= 400 or not isinstance(payload, list):
            return {"daemon": "ok", "running": 0, "exited": 0, "containers": []}
        containers = []
        running = 0
        exited = 0
        for item in payload:
            state = str(item.get("State") or "unknown").lower()
            if state == "running":
                running += 1
            elif state in {"exited", "dead"}:
                exited += 1
            containers.append(
                {
                    "name": _container_name(item),
                    "state": state,
                    "status": str(item.get("Status") or ""),
                }
            )
        return {
            "daemon": "ok",
            "running": running,
            "exited": exited,
            "containers": containers,
        }

    def _cpu(self) -> dict:
        loadavg = self._read_text("loadavg")
        stat = self._read_text("stat")
        if not loadavg:
            return {}
        try:
            load1 = float(loadavg.split()[0])
        except (IndexError, ValueError):
            return {}
        ncpus = sum(
            1
            for line in (stat or "").splitlines()
            if line.startswith("cpu") and not line.startswith("cpu ")
        )
        percent = round((load1 / ncpus) * 100, 1) if ncpus else None
        result: dict = {"load1": load1}
        if percent is not None:
            result["percent"] = percent
        return result

    def _memory(self) -> dict:
        raw = self._read_text("meminfo")
        if not raw:
            return {}
        values: dict[str, int] = {}
        for line in raw.splitlines():
            name, _, rest = line.partition(":")
            if name in {"MemTotal", "MemAvailable"}:
                try:
                    values[name] = int(rest.strip().split()[0]) * 1024
                except (IndexError, ValueError):
                    continue
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return {}
        used = max(0, total - available)
        return {
            "total_bytes": total,
            "used_bytes": used,
            "percent": round(used / total * 100, 1),
        }

    def _disk(self) -> dict:
        try:
            total, used, _free = self._disk_usage(self.host_root)
        except OSError:
            return {}
        if not total:
            return {}
        return {
            "total_bytes": total,
            "used_bytes": used,
            "percent": round(used / total * 100, 1),
        }

    def _read_text(self, name: str) -> str:
        path = Path(self.host_proc) / name
        try:
            return path.read_text()
        except OSError:
            return ""

    def _docker_http(self, path: str) -> tuple[int, Any]:
        transport = httpx.HTTPTransport(uds=self.docker_sock)
        with httpx.Client(
            transport=transport, base_url="http://localhost", timeout=5.0
        ) as client:
            if path == "/_ping":
                response = client.get("/_ping")
                return response.status_code, response.text
            response = client.get("/containers/json", params={"all": "true"})
            try:
                payload: Any = response.json()
            except ValueError:
                payload = []
            return response.status_code, payload

    @staticmethod
    def _statvfs(path: str) -> tuple[int, int, int]:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free
