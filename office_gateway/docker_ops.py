from __future__ import annotations

from typing import Any

_COMPOSE_SERVICE = "com.docker.compose.service"
_LOG_LINE_MAX = 500
LIST_CAP = 500
_NOT_RUNNING_CAP = 20
_NAMES_CAP = 80


def normalize_container_status(display: str) -> tuple[str, str | None]:
    raw = str(display or "unknown")
    low = raw.lower()
    health = "unhealthy" if "unhealthy" in low else None
    if low.startswith("up") or low == "running" or low.startswith("running"):
        return "running", health
    if "exited" in low:
        return "exited", health
    if "dead" in low:
        return "dead", health
    if "created" in low:
        return "created", health
    if "paused" in low:
        return "paused", health
    if "restarting" in low:
        return "restarting", health
    first = low.split()[0].rstrip("(") if low else "unknown"
    if first in {"running", "exited", "dead", "created", "paused", "restarting"}:
        return first, health
    return raw, health


def _container_status_health(
    state_val,
    *,
    item_status: str = "",
) -> tuple[str, str | None]:
    health: str | None = None
    if isinstance(state_val, dict):
        display = str(state_val.get("Status") or item_status or "unknown")
        health_obj = state_val.get("Health")
        if isinstance(health_obj, dict) and health_obj.get("Status"):
            health = str(health_obj.get("Status"))
        status, parsed_health = normalize_container_status(display)
        if health is None and parsed_health:
            health = parsed_health
        return status, health
    if isinstance(state_val, str):
        status, parsed_health = normalize_container_status(state_val)
        if parsed_health:
            health = parsed_health
        elif item_status:
            _, parsed_health = normalize_container_status(item_status)
            if parsed_health:
                health = parsed_health
        return status, health
    display = str(item_status or "unknown")
    return normalize_container_status(display)


def _parse_port_key(key: str) -> tuple[int, str] | None:
    port_s, _, proto = str(key).partition("/")
    try:
        return int(port_s), (proto or "tcp")
    except ValueError:
        return None


def strip_ports(raw: dict) -> list[dict]:
    settings = raw.get("NetworkSettings") if isinstance(raw.get("NetworkSettings"), dict) else {}
    ports = (settings or {}).get("Ports")
    if ports is None:
        ports = raw.get("Ports")
    out: list[dict] = []
    if isinstance(ports, dict):
        for key, bindings in ports.items():
            parsed = _parse_port_key(key)
            if parsed is None:
                continue
            container_port, protocol = parsed
            if not bindings:
                out.append(
                    {
                        "container_port": container_port,
                        "protocol": protocol,
                        "host_ip": "",
                        "host_port": None,
                    }
                )
                continue
            for bind in bindings:
                if not isinstance(bind, dict):
                    continue
                host_port = bind.get("HostPort")
                out.append(
                    {
                        "container_port": container_port,
                        "protocol": protocol,
                        "host_ip": str(bind.get("HostIp") or ""),
                        "host_port": int(host_port) if str(host_port).isdigit() else None,
                    }
                )
        return out
    if isinstance(ports, list):
        for item in ports:
            if not isinstance(item, dict):
                continue
            public = item.get("PublicPort")
            out.append(
                {
                    "container_port": int(item.get("PrivatePort") or 0),
                    "protocol": str(item.get("Type") or "tcp"),
                    "host_ip": str(item.get("IP") or ""),
                    "host_port": int(public) if public else None,
                }
            )
    return out


def strip_networks(raw: dict) -> list[dict]:
    settings = raw.get("NetworkSettings") if isinstance(raw.get("NetworkSettings"), dict) else {}
    nets = (settings or {}).get("Networks")
    if not isinstance(nets, dict):
        nets = raw.get("Networks")
    if not isinstance(nets, dict):
        return []
    rows = []
    for name, info in nets.items():
        data = info if isinstance(info, dict) else {}
        rows.append({"name": str(name), "ipv4": str(data.get("IPAddress") or "")})
    return rows


def strip_inspect(raw: dict) -> dict:
    name = str(raw.get("Name", "")).lstrip("/")
    state = raw.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    image = (raw.get("Config") or {}).get("Image") or (raw.get("Image") or "")
    labels = (raw.get("Config") or {}).get("Labels") or {}
    return {
        "name": name,
        "status": state.get("Status", "unknown"),
        "health": health,
        "image": image,
        "compose_service": str(labels.get(_COMPOSE_SERVICE) or ""),
        "ports": strip_ports(raw),
        "networks": strip_networks(raw),
        "env_names": sorted(
            {str(item).split("=", 1)[0] for item in ((raw.get("Config") or {}).get("Env") or [])}
        )[:60],
        "restart_count": int(raw.get("RestartCount") or 0),
        "started_at": str(state.get("StartedAt") or ""),
    }


def strip_container(item) -> dict:
    if isinstance(item, dict):
        names = item.get("Names") or []
        name = str(names[0] if names else item.get("Name") or "").lstrip("/")
        attrs = item
        item_status = str(item.get("Status") or "")
        status, health = _container_status_health(
            item.get("State"), item_status=item_status
        )
        image = item.get("Image") or ""
    else:
        attrs = getattr(item, "attrs", {}) or {}
        name = str(getattr(item, "name", "") or str(attrs.get("Name", "")).lstrip("/"))
        item_status = str(getattr(item, "status", "") or attrs.get("Status") or "")
        status, health = _container_status_health(
            attrs.get("State"), item_status=item_status
        )
        image = (attrs.get("Config") or {}).get("Image") or attrs.get("Image") or ""
    labels = attrs.get("Config", {}).get("Labels") or attrs.get("Labels") or {}
    return {
        "name": name,
        "status": status,
        "health": health,
        "image": str(image),
        "compose_service": str(labels.get(_COMPOSE_SERVICE) or ""),
        "ports": strip_ports(attrs),
        "networks": strip_networks(attrs),
    }


def strip_network(item) -> dict:
    attrs = item if isinstance(item, dict) else (getattr(item, "attrs", {}) or {})
    name = str(attrs.get("Name") or getattr(item, "name", "") or "")
    attached = attrs.get("Containers") or {}
    names = []
    if isinstance(attached, dict):
        for meta in attached.values():
            if isinstance(meta, dict) and meta.get("Name"):
                names.append(str(meta["Name"]).lstrip("/"))
            elif isinstance(meta, str):
                names.append(meta.lstrip("/"))
    names = sorted(set(names))
    return {
        "name": name,
        "driver": str(attrs.get("Driver") or ""),
        "scope": str(attrs.get("Scope") or ""),
        "container_names": names,
    }


class DockerOps:
    def __init__(self, client=None):
        self._client = client

    def _client_or_docker(self):
        if self._client is not None:
            return self._client
        import docker  # optional runtime dep

        return docker.from_env()

    def inspect(self, name: str) -> dict:
        try:
            client = self._client_or_docker()
            if hasattr(client, "inspect_container"):
                raw = client.inspect_container(name)
            else:
                raw = client.api.inspect_container(name)
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
        return strip_inspect(raw)

    def logs(self, name: str, tail: int = 80) -> dict:
        try:
            client = self._client_or_docker()
            if hasattr(client, "containers"):
                raw = client.containers.get(name).logs(
                    stdout=True, stderr=True, tail=tail
                )
            elif hasattr(client, "logs"):
                raw = client.logs(name, stdout=True, stderr=True, tail=tail)
            else:
                raw = client.api.logs(name, stdout=True, stderr=True, tail=tail)
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        lines = []
        for line in text.splitlines()[-tail:]:
            if len(line) > _LOG_LINE_MAX:
                line = line[:_LOG_LINE_MAX]
            lines.append(line)
        return {"name": name, "tail": len(lines), "lines": lines}

    def exec_run(self, name: str, cmd: list[str], *, user: str = "") -> dict[str, Any]:
        try:
            client = self._client_or_docker()
            container = client.containers.get(name)
            kwargs: dict[str, Any] = {"demux": True}
            if user:
                kwargs["user"] = user
            result = container.exec_run(cmd, **kwargs)
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "no such" in message:
                raise ValueError(f"container not found: {name}") from exc
            raise RuntimeError(f"docker exec failed: {name}") from exc
        code = getattr(result, "exit_code", None)
        output = getattr(result, "output", None)
        if code is None:
            code, output = result[0], result[1]
        if isinstance(output, tuple):
            stdout, stderr = output[0] or b"", output[1] or b""
        elif isinstance(output, (bytes, bytearray)):
            stdout, stderr = bytes(output), b""
        else:
            stdout, stderr = str(output or "").encode(), b""
        return {
            "exit_code": int(code),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

    def restart(self, name: str) -> None:
        try:
            client = self._client_or_docker()
            if hasattr(client, "restart"):
                client.restart(name)
            else:
                container = client.containers.get(name)
                container.restart()
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc

    def list_containers(self, all: bool = True) -> dict:
        client = self._client_or_docker()
        if hasattr(client, "containers"):
            items = client.containers.list(all=all)
        else:
            items = client.api.containers(all=all)
        containers = [strip_container(item) for item in items]
        truncated = len(containers) > LIST_CAP
        if truncated:
            containers = containers[:LIST_CAP]
        running = sum(
            1 for c in containers if str(c.get("status", "")).lower() == "running"
        )
        return {
            "containers": containers,
            "running": running,
            "total": len(containers),
            "truncated": truncated,
        }

    def list_networks(self) -> dict:
        client = self._client_or_docker()
        if hasattr(client, "networks"):
            items = client.networks.list()
        else:
            items = client.api.networks()
        networks = [strip_network(item) for item in items]
        truncated = len(networks) > LIST_CAP
        if truncated:
            networks = networks[:LIST_CAP]
        return {"networks": networks, "truncated": truncated}


def compact_listing(containers: list[dict]) -> dict:
    running = 0
    exited = 0
    unhealthy: list[str] = []
    not_running: list[str] = []
    for row in containers:
        name = str(row.get("name") or "")
        status, parsed_health = normalize_container_status(str(row.get("status") or ""))
        health = row.get("health") or parsed_health
        if status == "running":
            running += 1
        else:
            not_running.append(f"{name} ({status})")
        if status in {"exited", "dead"}:
            exited += 1
        if "unhealthy" in str(health or "").lower():
            unhealthy.append(name)
    names = sorted(str(row.get("name") or "") for row in containers)
    return {
        "running": running,
        "total": len(containers),
        "exited": exited,
        "unhealthy": unhealthy[:_NOT_RUNNING_CAP],
        "not_running": not_running[:_NOT_RUNNING_CAP],
        "names": names[:_NAMES_CAP],
        "names_truncated": len(names) > _NAMES_CAP,
    }
