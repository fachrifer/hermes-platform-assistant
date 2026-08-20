from __future__ import annotations


def panel_url(base: str, dashboard_uid: str, panel_id: int) -> str:
    return f"{base.rstrip('/')}/d/{dashboard_uid}?viewPanel={panel_id}"


def build_links(*, base: str, dashboards: dict[str, str], panels: dict[str, int]) -> list[dict]:
    if not base:
        return []
    links = []
    for name, uid in sorted(dashboards.items()):
        pid = panels.get(name)
        if pid is None:
            links.append({"name": name, "url": f"{base.rstrip('/')}/d/{uid}"})
        else:
            links.append({"name": name, "url": panel_url(base, uid, pid)})
    return links
