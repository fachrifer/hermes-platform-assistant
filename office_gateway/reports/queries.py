from __future__ import annotations

import re
from dataclasses import dataclass

WINDOWS = ("5m", "1h", "24h", "7d", "30d")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9.:_-]{1,64}$")
_FS = 'fstype!~"tmpfs|overlay|squashfs|devtmpfs"'


@dataclass(frozen=True)
class NamedQuery:
    description: str
    template: str
    uses_instance: bool = False
    default_window: str | None = None


QUERIES: dict[str, NamedQuery] = {
    "targets_up": NamedQuery("Scrape targets up, per job", "sum by (job) (up)"),
    "targets_down": NamedQuery("Scrape targets currently down", "up == 0"),
    "cpu_busy_pct": NamedQuery(
        "CPU busy percent per instance",
        '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"{sel}}[{window}])))',
        uses_instance=True,
        default_window="5m",
    ),
    "mem_used_pct": NamedQuery(
        "RAM used percent per instance",
        "100 * (1 - node_memory_MemAvailable_bytes{isel} / node_memory_MemTotal_bytes{isel})",
        uses_instance=True,
    ),
    "disk_used_pct": NamedQuery(
        "Filesystem used percent per instance and mountpoint",
        f"100 * (1 - node_filesystem_avail_bytes{{{_FS}{{sel}}}} / node_filesystem_size_bytes{{{_FS}{{sel}}}})",
        uses_instance=True,
    ),
    "load_per_core": NamedQuery(
        "1-minute load divided by CPU cores",
        'node_load1{isel} / on (instance) count by (instance) (node_cpu_seconds_total{mode="idle"{sel}})',
        uses_instance=True,
    ),
    "probe_success": NamedQuery("Blackbox probe success (1 up, 0 down)", "probe_success{isel}", uses_instance=True),
    "availability_pct": NamedQuery(
        "Probe availability percent over a window",
        "100 * avg_over_time(probe_success{isel}[{window}])",
        uses_instance=True,
        default_window="24h",
    ),
}


def render(name: str, instance: str | None = None, window: str | None = None) -> str:
    query = QUERIES.get(name)
    if query is None:
        raise ValueError("unknown query")
    sel = isel = ""
    if instance:
        if not query.uses_instance:
            raise ValueError("query takes no instance")
        if not _INSTANCE_RE.fullmatch(instance):
            raise ValueError("instance has invalid characters")
        pattern = instance.replace(".", "\\\\.")
        sel = f',instance=~"^{pattern}(:[0-9]+)?$"'
        isel = "{" + sel[1:] + "}"
    chosen_window = window or query.default_window or "5m"
    if chosen_window not in WINDOWS:
        raise ValueError("invalid window")
    return (
        query.template.replace("{sel}", sel).replace("{isel}", isel).replace("{window}", chosen_window)
    )
