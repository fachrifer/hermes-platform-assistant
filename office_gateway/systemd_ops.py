from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Callable, Optional

from office_gateway.docker_ops import LIST_CAP
from office_gateway.k8s_ops import AdapterNotConfigured

ListUnitsFn = Callable[[str], list]

_UNIT_FIELDS = ("id", "description", "load_state", "active_state", "sub_state")


def strip_unit(raw: dict) -> dict[str, str]:
    return {key: str(raw.get(key) or "") for key in _UNIT_FIELDS}


def _from_tuple(row: tuple) -> dict[str, str]:
    return {
        "id": str(row[0] if len(row) > 0 else ""),
        "description": str(row[1] if len(row) > 1 else ""),
        "load_state": str(row[2] if len(row) > 2 else ""),
        "active_state": str(row[3] if len(row) > 3 else ""),
        "sub_state": str(row[4] if len(row) > 4 else ""),
    }


def _connect_and_list(bus_path: Path) -> list:
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.wrappers import unwrap_msg
    except ImportError as exc:
        raise AdapterNotConfigured() from exc

    address = DBusAddress(
        "/org/freedesktop/systemd1",
        bus_name="org.freedesktop.systemd1",
        interface="org.freedesktop.systemd1.Manager",
    )
    conn = open_dbus_connection(bus=f"unix:path={bus_path}")
    try:
        reply = conn.send_and_get_reply(new_method_call(address, "ListUnits"))
        return list(unwrap_msg(reply)[0])
    finally:
        conn.close()


class SystemdOps:
    def __init__(
        self,
        list_fn: Optional[ListUnitsFn] = None,
        bus_path: str = "/run/dbus/system_bus_socket",
        cgroup_root: str = "/sys/fs/cgroup",
    ) -> None:
        self._list_fn = list_fn
        self._bus_path = bus_path
        self._cgroup_root = cgroup_root

    def list_units(self, unit_type: str = "service") -> dict[str, Any]:
        suffix = f".{unit_type}" if unit_type.strip() else ""
        rows = self._list_fn(unit_type) if self._list_fn else self._list_live()
        units: list[dict[str, str]] = []
        for row in rows:
            data = strip_unit(row) if isinstance(row, dict) else strip_unit(_from_tuple(tuple(row)))
            if suffix and not data["id"].endswith(suffix):
                continue
            units.append(data)
        truncated = len(units) > LIST_CAP
        return {"units": units[:LIST_CAP], "truncated": truncated}

    def _bus_is_socket(self) -> bool:
        path = Path(self._bus_path)
        try:
            mode = path.stat().st_mode
        except OSError:
            return False
        return stat.S_ISSOCK(mode)

    def _list_live(self) -> list:
        if not self._bus_is_socket():
            raise AdapterNotConfigured()
        try:
            return _connect_and_list(Path(self._bus_path))
        except Exception as exc:
            rows = self._list_via_cgroup()
            if rows:
                return rows
            if isinstance(exc, (AdapterNotConfigured, ImportError, OSError)):
                raise AdapterNotConfigured() from exc
            raise

    def _list_via_cgroup(self) -> list:
        root = Path(self._cgroup_root)
        if not root.is_dir():
            return []
        bases = [
            root / "system.slice",
            root / "systemd" / "system.slice",
            root / "unified" / "system.slice",
        ]
        found: list[dict[str, str]] = []
        seen: set[str] = set()
        for base in bases:
            if not base.is_dir():
                continue
            for dirpath, dirnames, _filenames in os.walk(base, followlinks=False):
                rel = Path(dirpath).relative_to(base)
                if len(rel.parts) > 4:
                    dirnames.clear()
                    continue
                name = Path(dirpath).name
                if not name.endswith(".service") or name in seen:
                    continue
                seen.add(name)
                found.append(
                    {
                        "id": name,
                        "description": "",
                        "load_state": "loaded",
                        "active_state": "active",
                        "sub_state": "running",
                    }
                )
        return found
