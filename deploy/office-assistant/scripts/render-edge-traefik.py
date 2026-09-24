#!/usr/bin/env python3
"""Render Traefik dynamic YAML from deploy/office-assistant/edge/edge-routes."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root(script_dir: Path) -> Path:
    ctx = os.environ.get("OFFICE_GATEWAY_CONTEXT")
    if ctx:
        candidate = Path(ctx)
        if not candidate.is_absolute():
            candidate = (script_dir.parent / ctx).resolve()
        return candidate
    deploy = script_dir.parent
    if (deploy / "office_gateway").is_dir():
        return deploy
    return deploy.parent.parent


def _ensure_gateway_import(script_dir: Path) -> None:
    root = _repo_root(script_dir)
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


_here = Path(__file__).resolve().parent
_ensure_gateway_import(_here)

from office_gateway.edge_ops import (  # noqa: E402
    EdgeRoute,
    _atomic_write,
    parse_edge_routes,
    render_traefik_dynamic,
)

__all__ = ["EdgeRoute", "parse_edge_routes", "render_traefik_dynamic"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    deploy = _here.parent
    parser.add_argument(
        "--routes",
        type=Path,
        default=deploy / "edge" / "edge-routes",
        help="path to edge-routes file",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=deploy / "edge" / "traefik-dynamic" / "routes.yml",
        help="output Traefik dynamic routes YAML path",
    )
    parser.add_argument(
        "--host-alias",
        default="host.docker.internal",
        help="rewrite 127.0.0.1/localhost to this host for Docker",
    )
    args = parser.parse_args(argv)
    try:
        text = args.routes.read_text(encoding="utf-8")
        routes = parse_edge_routes(text)
        rendered = render_traefik_dynamic(routes, host_alias=args.host_alias)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _atomic_write(args.out, rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
