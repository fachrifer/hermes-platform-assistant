from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

RESERVED_PREFIXES = (
    "/dash/",
    "/api/",
    "/auth/",
    "/assets/",
    "/avatars/",
    "/fonts/",
    "/fonts-terminal/",
    "/dashboard-plugins/",
    "/login",
    "/bots/",
)


class AdapterNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class EdgeRoute:
    name: str
    path: str
    upstream: str
    websocket: bool
    strip_prefix: bool = True


def _is_reserved(path: str) -> bool:
    if path == "/login" or path.startswith("/login?") or path.startswith("/login/"):
        return True
    for prefix in RESERVED_PREFIXES:
        if prefix == "/login":
            continue
        if path == prefix or path.startswith(prefix):
            return True
    return False


def parse_edge_routes(text: str) -> list[EdgeRoute]:
    routes: list[EdgeRoute] = []
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in (4, 5):
            raise ValueError(
                f"line {lineno}: expected 4 or 5 fields "
                "(name path upstream websocket [strip_prefix])"
            )
        name, path, upstream, flag = parts[:4]
        strip_flag = parts[4] if len(parts) == 5 else "1"
        if not path.startswith("/") or not path.endswith("/"):
            raise ValueError(f"line {lineno}: path must start and end with '/'")
        if _is_reserved(path):
            raise ValueError(f"line {lineno}: path {path!r} is reserved")
        if ":" not in upstream:
            raise ValueError(f"line {lineno}: upstream must be host:port")
        if flag not in ("0", "1"):
            raise ValueError(f"line {lineno}: websocket flag must be 0 or 1")
        if strip_flag not in ("0", "1"):
            raise ValueError(f"line {lineno}: strip_prefix flag must be 0 or 1")
        if name in seen_names:
            raise ValueError(f"line {lineno}: duplicate name {name!r}")
        if path in seen_paths:
            raise ValueError(f"line {lineno}: duplicate path {path!r}")
        seen_names.add(name)
        seen_paths.add(path)
        routes.append(
            EdgeRoute(
                name=name,
                path=path,
                upstream=upstream,
                websocket=flag == "1",
                strip_prefix=strip_flag == "1",
            )
        )
    return routes


def _proxy_target(upstream: str, host_alias: str) -> str:
    host, sep, port = upstream.rpartition(":")
    if not sep or not port:
        raise ValueError(f"upstream must be host:port, got {upstream!r}")
    if host in ("127.0.0.1", "localhost", "::1"):
        host = host_alias
    return f"{host}:{port}"


def render_traefik_dynamic(
    routes: list[EdgeRoute],
    *,
    host_alias: str = "host.docker.internal",
) -> str:
    """Render Traefik v3 file-provider YAML for edge-routes (HTTP path proxies)."""
    chunks = [
        "# Generated from edge-routes — do not edit by hand.",
        "http:",
        "  routers:",
    ]
    middlewares: list[str] = []
    services: list[str] = []
    for route in routes:
        target = _proxy_target(route.upstream, host_alias)
        prefix = route.path.rstrip("/")
        rule = f"PathPrefix(`{prefix}`)"
        chunks.append(f"    {route.name}:")
        chunks.append(f'      rule: "{rule}"')
        chunks.append("      entryPoints:")
        chunks.append("        - websecure")
        chunks.append("      tls: {}")
        chunks.append(f"      service: {route.name}")
        chunks.append("      priority: 20")
        if route.strip_prefix:
            mw = f"{route.name}-strip"
            chunks.append("      middlewares:")
            chunks.append(f"        - {mw}")
            middlewares.append(f"    {mw}:")
            middlewares.append("      stripPrefix:")
            middlewares.append("        prefixes:")
            middlewares.append(f'          - "{prefix}"')
        services.append(f"    {route.name}:")
        services.append("      loadBalancer:")
        services.append("        servers:")
        services.append(f'          - url: "http://{target}"')
        if route.websocket:
            # Traefik passes Upgrade by default; sticky not required for lab UIs.
            pass

    if middlewares:
        chunks.append("  middlewares:")
        chunks.extend(middlewares)
    chunks.append("  services:")
    if services:
        chunks.extend(services)
    else:
        chunks.append("    {}")
    chunks.append("")
    return "\n".join(chunks)


# Back-compat name used by older tests/scripts — Traefik dynamic YAML.
def render_locations(
    routes: list[EdgeRoute],
    *,
    host_alias: str = "host.docker.internal",
) -> str:
    return render_traefik_dynamic(routes, host_alias=host_alias)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


class EdgeRoutesOps:
    def __init__(
        self,
        routes_path: str | None,
        locations_path: str | None,
        host_alias: str = "host.docker.internal",
    ):
        self._routes_path = Path(routes_path) if routes_path else None
        self._locations_path = Path(locations_path) if locations_path else None
        self._host_alias = host_alias

    def configured(self) -> bool:
        return self._routes_path is not None and self._locations_path is not None

    def _require_configured(self) -> tuple[Path, Path]:
        if not self.configured():
            raise AdapterNotConfigured()
        assert self._routes_path is not None
        assert self._locations_path is not None
        return self._routes_path, self._locations_path

    def read_text(self) -> str:
        routes_path, _ = self._require_configured()
        return routes_path.read_text(encoding="utf-8")

    def list_routes(self) -> list[EdgeRoute]:
        return parse_edge_routes(self.read_text())

    def validate(self, content: str) -> list[EdgeRoute]:
        return parse_edge_routes(content)

    def apply(self, content: str) -> list[EdgeRoute]:
        routes = self.validate(content)
        rendered = render_locations(routes, host_alias=self._host_alias)
        routes_path, locations_path = self._require_configured()
        _atomic_write(routes_path, content)
        _atomic_write(locations_path, rendered)
        return routes
