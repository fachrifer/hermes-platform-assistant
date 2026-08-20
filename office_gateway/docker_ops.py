from __future__ import annotations


def strip_inspect(raw: dict) -> dict:
    name = str(raw.get("Name", "")).lstrip("/")
    state = raw.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    image = (raw.get("Config") or {}).get("Image") or (raw.get("Image") or "")
    return {
        "name": name,
        "status": state.get("Status", "unknown"),
        "health": health,
        "image": image,
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
        client = self._client_or_docker()
        try:
            if hasattr(client, "inspect_container"):
                raw = client.inspect_container(name)
            else:
                raw = client.api.inspect_container(name)
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
        return strip_inspect(raw)

    def restart(self, name: str) -> None:
        client = self._client_or_docker()
        try:
            if hasattr(client, "restart"):
                client.restart(name)
            else:
                container = client.containers.get(name)
                container.restart()
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
