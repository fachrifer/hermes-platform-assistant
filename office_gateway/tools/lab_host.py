from __future__ import annotations

import asyncio

from office_gateway.docker_ops import compact_listing
from office_gateway.tools.common import logs_window, pending_view
from office_gateway.tools.core import Param, Tool, ToolError

LAB = frozenset({"lab-host"})
_LINES = Param("integer", "number of newest log lines (1-100)", minimum=1, maximum=100, default=30)
_CONTAINS = Param("string", "optional case-insensitive substring filter", max_length=60)


async def _names(ctx) -> list[str]:
    return await asyncio.to_thread(ctx.actions.container_names)


async def _require_container(ctx, name: str) -> None:
    names = await _names(ctx)
    if name not in names:
        raise ToolError("invalid_argument", "unknown container", names)


async def _list_containers(ctx, args, role):
    listing = await asyncio.to_thread(ctx.docker.list_containers)
    return compact_listing(listing.get("containers") or [])


async def _inspect_container(ctx, args, role):
    await _require_container(ctx, args["name"])
    info = await asyncio.to_thread(ctx.docker.inspect, args["name"])
    ports = [
        f"{p.get('host_ip') or '*'}:{p.get('host_port')}->{p.get('container_port')}/{p.get('protocol')}"
        if p.get("host_port")
        else f"{p.get('container_port')}/{p.get('protocol')}"
        for p in info.get("ports", [])
    ][:10]
    return {
        "name": info.get("name"),
        "status": info.get("status"),
        "health": info.get("health"),
        "image": info.get("image"),
        "compose_service": info.get("compose_service"),
        "restart_count": info.get("restart_count", 0),
        "started_at": info.get("started_at", ""),
        "ports": ports,
        "networks": [n.get("name") for n in info.get("networks", [])][:5],
        "env_names": info.get("env_names", []),
    }


async def _tail_logs(ctx, args, role):
    await _require_container(ctx, args["name"])
    fetch = 500 if args.get("contains") else args["lines"]
    raw = await asyncio.to_thread(ctx.docker.logs, args["name"], fetch)
    return {"name": args["name"], **logs_window(raw.get("lines", []), args["lines"], args.get("contains"))}


def _pct(used: float, total: float) -> float:
    return round(used / total * 100, 1) if total else 0.0


async def _host_resources(ctx, args, role):
    usage = await asyncio.to_thread(ctx.proc.host_usage)
    mem = usage["memory_bytes"]
    swap = usage["swap_bytes"]
    disk = usage["disk_bytes"]
    cores = max(1, int(usage.get("cpu_count") or 1))
    load = usage.get("loadavg", [0, 0, 0])
    return {
        "load": load,
        "cores": cores,
        "load_per_core": round(float(load[0]) / cores, 2),
        "mem_used_pct": _pct(mem["total"] - mem["available"], mem["total"]),
        "swap_used_pct": _pct(swap["used"], swap["total"]),
        "disk_used_pct": _pct(disk["used"], disk["total"]),
        "disk_path": disk.get("path", ""),
        "uptime_hours": round(float(usage.get("uptime_seconds") or 0) / 3600, 1),
    }


async def _list_host_services(ctx, args, role):
    listing = await asyncio.to_thread(ctx.systemd.list_units, "service")
    units = listing.get("units", [])
    failed = [u for u in units if u.get("active_state") == "failed"]
    state = args["state"]
    if state == "failed":
        chosen = failed
    elif state == "running":
        chosen = [u for u in units if u.get("sub_state") == "running"]
    else:
        chosen = units
    return {
        "total": len(units),
        "failed": len(failed),
        "units": [f"{u['id']} ({u.get('active_state')}/{u.get('sub_state')})" for u in chosen][:40],
    }


async def _propose_restart(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose, role, "restart_service", args["container"], {"reason": args["reason"]}
    )
    return pending_view(action, ctx.config.console_url)


TOOLS = (
    Tool("list_containers", LAB, "Counts of Lab Docker containers, the ones not running or unhealthy, and all names.", {}, _list_containers),
    Tool(
        "inspect_container",
        LAB,
        "Summary of one Lab container (status, health, image, ports, restart count, env variable names only).",
        {"name": Param("string", "exact container name from list_containers", required=True, max_length=128)},
        _inspect_container,
    ),
    Tool(
        "tail_logs",
        LAB,
        "Newest redacted log lines of one Lab container. Use contains= to find errors.",
        {"name": Param("string", "exact container name", required=True, max_length=128), "lines": _LINES, "contains": _CONTAINS},
        _tail_logs,
    ),
    Tool("host_resources", LAB, "Lab host load, RAM, swap, disk and uptime.", {}, _host_resources),
    Tool(
        "list_host_services",
        LAB,
        "systemd services on the Lab host (default: failed only).",
        {"state": Param("string", "which units to list", enum=("failed", "running", "all"), default="failed")},
        _list_host_services,
    ),
    Tool(
        "propose_restart",
        LAB,
        "Propose restarting one Lab container. Creates a pending action for a human to approve in the console; "
        "it does NOT restart anything. Report the action_id to the supervisor.",
        {
            "container": Param("string", "exact container name", required=True, max_length=128),
            "reason": Param("string", "one-line reason shown to the approver", required=True, max_length=200),
        },
        _propose_restart,
    ),
)
