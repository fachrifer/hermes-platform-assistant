from __future__ import annotations

import os
import pwd
import time
from pathlib import Path
from typing import Any, Callable, Optional

from office_gateway.k8s_ops import AdapterNotConfigured

PROCESS_LIMIT_DEFAULT = 40
PROCESS_LIMIT_MAX = 200


def parse_stat(text: str) -> dict[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("invalid stat")
    pid = int(text[:start].strip())
    comm = text[start + 1 : end]
    fields = text[end + 2 :].split()
    return {
        "pid": pid,
        "comm": comm,
        "state": fields[0] if fields else "?",
        "utime": int(fields[11]) if len(fields) > 11 else 0,
        "stime": int(fields[12]) if len(fields) > 12 else 0,
        "rss_pages": int(fields[21]) if len(fields) > 21 else 0,
    }


def strip_process(
    parsed: dict[str, Any],
    *,
    user: str,
    cpu_percent: float,
    page_size: int,
) -> dict[str, Any]:
    return {
        "pid": int(parsed["pid"]),
        "user": user,
        "comm": str(parsed["comm"]),
        "state": str(parsed["state"]),
        "cpu_percent": round(float(cpu_percent), 1),
        "rss_bytes": int(parsed["rss_pages"]) * int(page_size),
    }


def _mem_kb(meminfo: str, key: str) -> int:
    prefix = key + ":"
    for line in meminfo.splitlines():
        if line.startswith(prefix):
            parts = line.split()
            return int(parts[1])
    return 0


class ProcOps:
    def __init__(
        self,
        proc_root: str = "/proc",
        sample_seconds: float = 0.2,
        sleeper: Optional[Callable[[float], None]] = None,
        page_size: Optional[int] = None,
        cpu_count: Optional[int] = None,
        disk_path: Optional[str] = None,
    ) -> None:
        self._proc_root = Path(proc_root)
        self._sample_seconds = sample_seconds
        self._sleeper = sleeper or time.sleep
        self._page_size = page_size or int(os.sysconf("SC_PAGE_SIZE") or 4096)
        self._cpu_count = cpu_count or (os.cpu_count() or 1)
        self._disk_path = disk_path

    def _require_proc(self) -> Path:
        if not self._proc_root.is_dir():
            raise AdapterNotConfigured()
        return self._proc_root

    def _snapshot(self) -> dict[int, dict[str, Any]]:
        root = self._require_proc()
        out: dict[int, dict[str, Any]] = {}
        for entry in root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                parsed = parse_stat((entry / "stat").read_text())
            except (OSError, ValueError):
                continue
            user = str(parsed.get("uid") or "")
            status_path = entry / "status"
            try:
                for line in status_path.read_text().splitlines():
                    if line.startswith("Uid:"):
                        uid = int(line.split()[1])
                        try:
                            user = pwd.getpwuid(uid).pw_name
                        except KeyError:
                            user = str(uid)
                        break
            except OSError:
                user = "?"
            parsed["user"] = user
            out[parsed["pid"]] = parsed
        return out

    def list_processes(self, limit: int = PROCESS_LIMIT_DEFAULT) -> dict[str, Any]:
        cap = min(max(1, int(limit)), PROCESS_LIMIT_MAX)
        first = self._snapshot()
        dt = max(self._sample_seconds, 0.0)
        if dt:
            self._sleeper(dt)
        second = self._snapshot()
        ticks = float(os.sysconf("SC_CLK_TCK") or 100)
        rows: list[dict[str, Any]] = []
        for pid, now in second.items():
            before = first.get(pid)
            delta = 0
            if before is not None and dt > 0:
                delta = (now["utime"] + now["stime"]) - (before["utime"] + before["stime"])
            cpu = 0.0
            if dt > 0 and ticks > 0:
                cpu = (delta / ticks) / dt * 100.0
            rows.append(
                strip_process(
                    now,
                    user=str(now.get("user") or "?"),
                    cpu_percent=cpu,
                    page_size=self._page_size,
                )
            )
        rows.sort(key=lambda row: (-row["cpu_percent"], -row["rss_bytes"]))
        truncated = len(rows) > cap
        return {"processes": rows[:cap], "truncated": truncated}

    def _disk_target(self) -> str:
        if self._disk_path:
            return self._disk_path
        host_root = "/proc/1/root"
        if self._proc_root.as_posix() == "/proc" and os.path.isdir(host_root):
            return host_root
        return "/"

    def host_usage(self) -> dict[str, Any]:
        root = self._require_proc()
        try:
            load_parts = (root / "loadavg").read_text().split()
            loadavg = [float(load_parts[0]), float(load_parts[1]), float(load_parts[2])]
        except (OSError, IndexError, ValueError) as exc:
            raise AdapterNotConfigured() from exc
        meminfo = (root / "meminfo").read_text()
        total = _mem_kb(meminfo, "MemTotal") * 1024
        available = _mem_kb(meminfo, "MemAvailable") * 1024
        free = _mem_kb(meminfo, "MemFree") * 1024
        swap_total = _mem_kb(meminfo, "SwapTotal") * 1024
        swap_free = _mem_kb(meminfo, "SwapFree") * 1024
        try:
            uptime = float((root / "uptime").read_text().split()[0])
        except (OSError, IndexError, ValueError):
            uptime = 0.0
        disk_path = self._disk_target()
        try:
            disk = os.statvfs(disk_path)
            disk_total = disk.f_frsize * disk.f_blocks
            disk_free = disk.f_frsize * disk.f_bavail
            disk_used = disk_total - disk.f_frsize * disk.f_bfree
        except OSError:
            disk_total = disk_used = disk_free = 0
            disk_path = ""
        return {
            "loadavg": loadavg,
            "cpu_count": int(self._cpu_count),
            "memory_bytes": {
                "total": total,
                "available": available,
                "free": free,
            },
            "swap_bytes": {
                "total": swap_total,
                "used": max(0, swap_total - swap_free),
            },
            "disk_bytes": {
                "total": disk_total,
                "used": max(0, disk_used),
                "free": disk_free,
                "path": disk_path,
            },
            "uptime_seconds": uptime,
        }
