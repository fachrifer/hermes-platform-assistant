"""Structure checks for the generated Fleet Overview dashboard (no Grafana needed)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = ROOT / "deploy" / "office-assistant" / "grafana"
DASHBOARD = GRAFANA / "fleet-overview.json"


def _load():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _walk(panels):
    for panel in panels:
        yield panel
        yield from _walk(panel.get("panels", []))


def test_json_is_up_to_date_with_the_generator():
    before = DASHBOARD.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(GRAFANA / "build_fleet_overview.py")], check=True, capture_output=True)
    try:
        assert DASHBOARD.read_text(encoding="utf-8") == before
    finally:
        DASHBOARD.write_text(before, encoding="utf-8", newline="\n")


def test_identity_is_kept_so_import_overwrites_the_existing_dashboard():
    data = _load()
    assert data["uid"] == "fleet-overview"
    assert data["title"].startswith("Fleet Overview")
    assert data["refresh"] == "1m"


def test_panels_have_unique_ids_and_do_not_overlap():
    cells = {}
    ids = []
    for panel in _walk(_load()["panels"]):
        ids.append(panel["id"])
        grid = panel["gridPos"]
        assert grid["x"] + grid["w"] <= 24, panel["id"]
        for y in range(grid["y"], grid["y"] + grid["h"]):
            for x in range(grid["x"], grid["x"] + grid["w"]):
                assert (x, y) not in cells, f"panel {panel['id']} overlaps {cells[(x, y)]}"
                cells[(x, y)] = panel["id"]
    assert len(ids) == len(set(ids))
    for y in range(max(y for _, y in cells) + 1):
        filled = sum(1 for x in range(24) if (x, y) in cells)
        assert filled == 24, f"row y={y} is only {filled}/24 wide"


def test_every_panel_uses_the_datasource_variable():
    for panel in _walk(_load()["panels"]):
        if panel["type"] == "row":
            continue
        assert panel["datasource"]["uid"] == "${datasource}", panel["id"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "${datasource}", panel["id"]


def test_no_panel_hides_series_with_a_saved_override():
    for panel in _walk(_load()["panels"]):
        for override in panel.get("fieldConfig", {}).get("overrides", []):
            for prop in override.get("properties", []):
                assert prop["id"] != "custom.hideFrom", f"panel {panel['id']} hides series"


def test_status_tiles_never_render_a_missing_target_as_healthy_or_blank():
    for panel in _walk(_load()["panels"]):
        if panel["type"] == "stat" and panel["title"] in {"Hosts", "Services"}:
            for target in panel["targets"]:
                assert target["expr"].endswith("or vector(-1)"), target["expr"]
            assert panel["fieldConfig"]["defaults"]["mappings"][0]["options"]["-1"]["text"] == "NO DATA"


def test_disk_panels_do_not_drop_boot():
    exprs = " ".join(t["expr"] for p in _walk(_load()["panels"]) if p["id"] in (27, 111, 112) for t in p["targets"])
    assert "/boot" not in exprs.split("mountpoint!~")[1].split('"')[1]
