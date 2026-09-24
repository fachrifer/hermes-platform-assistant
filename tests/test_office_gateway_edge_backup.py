from datetime import datetime, timedelta, timezone

import pytest

import office_gateway.edge_ops as edge_ops
from office_gateway.edge_ops import EdgeRoutesOps, RouteApplyError, route_diff

OLD = "grafana /grafana/ 10.0.0.1:3000 0\n"
NEW = "grafana /grafana/ 10.0.0.1:3000 0\nattu /attu/ 10.0.0.2:8000 1\n"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def _ops(tmp_path, clock=None):
    routes = tmp_path / "edge-routes"
    dynamic = tmp_path / "dynamic" / "routes.yml"
    routes.write_text(OLD, encoding="utf-8")
    return EdgeRoutesOps(str(routes), str(dynamic), clock=clock or Clock()), routes, dynamic


def test_apply_backs_up_previous_file_and_writes_both(tmp_path):
    ops, routes, dynamic = _ops(tmp_path)
    result = ops.apply(NEW)
    assert result["routes"] == 2
    assert result["backup"] == ops.list_backups()[0]
    assert ops.read_backup(result["backup"]) == OLD
    assert routes.read_text(encoding="utf-8") == NEW
    assert "attu" in dynamic.read_text(encoding="utf-8")


def test_keeps_only_newest_ten_backups(tmp_path):
    ops, _, _ = _ops(tmp_path)
    for i in range(12):
        ops.apply(OLD if i % 2 else NEW)
    backups = ops.list_backups()
    assert len(backups) == 10
    assert backups == sorted(backups, reverse=True)


def test_failed_render_restores_previous_routes(tmp_path, monkeypatch):
    ops, routes, _ = _ops(tmp_path)
    original = edge_ops.render_locations

    def render(route_list, **kwargs):
        if any(r.name == "boom" for r in route_list):
            raise ValueError("render failed")
        return original(route_list, **kwargs)

    monkeypatch.setattr(edge_ops, "render_locations", render)
    with pytest.raises(RouteApplyError) as info:
        ops.apply("boom /boom/ 10.0.0.9:80 0\n")
    assert info.value.rolled_back is True
    assert routes.read_text(encoding="utf-8") == OLD


def test_restore_newest_or_named_backup(tmp_path):
    ops, routes, _ = _ops(tmp_path)
    ops.apply(NEW)
    newest = ops.list_backups()[0]
    result = ops.restore()
    assert result["restored"] == newest
    assert routes.read_text(encoding="utf-8") == OLD
    with pytest.raises(ValueError, match="unknown backup"):
        ops.restore("edge-routes.nope")


def test_restore_without_backups_fails(tmp_path):
    ops, _, _ = _ops(tmp_path)
    with pytest.raises(ValueError, match="no backups"):
        ops.restore()


def test_read_backup_rejects_path_tricks(tmp_path):
    ops, _, _ = _ops(tmp_path)
    ops.apply(NEW)
    with pytest.raises(ValueError, match="unknown backup"):
        ops.read_backup("../edge-routes")


def test_approvals_path_is_reserved(tmp_path):
    ops, _, _ = _ops(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        ops.validate("x /approvals/ 10.0.0.1:80 0\n")


def test_route_diff_is_compact():
    diff = route_diff(OLD, NEW)
    assert "+attu /attu/ 10.0.0.2:8000 1" in diff
    assert route_diff(OLD, OLD) == []
