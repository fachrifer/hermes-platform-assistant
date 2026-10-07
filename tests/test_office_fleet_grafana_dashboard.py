"""Structure checks for the generated Grafana dashboards (no Grafana needed)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = ROOT / "deploy" / "office-assistant" / "grafana"
FLEET = GRAFANA / "fleet-overview.json"
MILVUS = GRAFANA / "milvus-monitor.json"
BOTH = [
    pytest.param(FLEET, "build_fleet_overview.py", id="fleet"),
    pytest.param(MILVUS, "build_milvus_monitor.py", id="milvus"),
]


def _load(path: Path = FLEET):
    return json.loads(path.read_text(encoding="utf-8"))


def _walk(panels):
    for panel in panels:
        yield panel
        yield from _walk(panel.get("panels", []))


@pytest.mark.parametrize("path,script", BOTH)
def test_json_is_up_to_date_with_the_generator(path, script):
    before = path.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(GRAFANA / script)], check=True, capture_output=True)
    try:
        assert path.read_text(encoding="utf-8") == before
    finally:
        path.write_text(before, encoding="utf-8", newline="\n")


def test_identity_is_kept_so_import_overwrites_the_existing_dashboards():
    fleet, milvus = _load(FLEET), _load(MILVUS)
    assert fleet["uid"] == "fleet-overview" and fleet["title"].startswith("Fleet Overview")
    assert milvus["uid"] == "de68d706-4a90-4ad8-b338-fb5d94b09af0"
    assert milvus["title"] == "Milvus & Server Monitor"
    assert fleet["refresh"] == "1m"


@pytest.mark.parametrize("path,script", BOTH)
def test_panels_have_unique_ids_and_do_not_overlap(path, script):
    cells = {}
    ids = []
    for panel in _walk(_load(path)["panels"]):
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


@pytest.mark.parametrize("path,script", BOTH)
def test_every_panel_uses_the_datasource_variable(path, script):
    for panel in _walk(_load(path)["panels"]):
        if panel["type"] == "row":
            continue
        assert panel["datasource"]["uid"] == "${datasource}", panel["id"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "${datasource}", panel["id"]


@pytest.mark.parametrize("path,script", BOTH)
def test_no_panel_hides_series_with_a_saved_override(path, script):
    for panel in _walk(_load(path)["panels"]):
        for override in panel.get("fieldConfig", {}).get("overrides", []):
            for prop in override.get("properties", []):
                assert prop["id"] != "custom.hideFrom", f"panel {panel['id']} hides series"


def test_status_tiles_never_render_a_missing_target_as_healthy_or_blank():
    for panel in _walk(_load(FLEET)["panels"]):
        if panel["type"] == "stat" and panel["title"] in {"Hosts", "Services"}:
            for target in panel["targets"]:
                assert target["expr"].endswith("or vector(-1)"), target["expr"]
            assert panel["fieldConfig"]["defaults"]["mappings"][0]["options"]["-1"]["text"] == "NO DATA"


def test_fleet_milvus_labels_say_which_endpoint_they_are():
    labels = {
        t["legendFormat"]
        for p in _walk(_load(FLEET)["panels"]) if p["type"] == "stat" and p["title"] in {"Hosts", "Services"}
        for t in p["targets"]
    }
    assert "Milvus PROD host 10.216.203.132" in labels and "Milvus PROD (API)" in labels
    assert "Milvus API" not in labels


def test_milvus_dashboard_is_scoped_by_the_server_label_not_a_hardcoded_ip():
    data = _load(MILVUS)
    assert any(v["name"] == "server" for v in data["templating"]["list"])
    for panel in _walk(data["panels"]):
        for target in panel.get("targets", []):
            assert "10.216.203.132" not in target["expr"], panel["id"]
            assert 'server="$server"' in target["expr"], panel["id"]


def test_disk_panels_do_not_drop_boot():
    exprs = " ".join(t["expr"] for p in _walk(_load(FLEET)["panels"]) if p["id"] in (27, 111, 112) for t in p["targets"])
    assert "/boot" not in exprs.split("mountpoint!~")[1].split('"')[1]
