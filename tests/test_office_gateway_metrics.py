import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from office_gateway.app import create_app
from office_gateway.config import GatewayConfig
from office_gateway.grafana_links import panel_url, build_links


def test_panel_url_joins_base_uid_and_id():
    url = panel_url("http://10.1.2.3:3000/", "abcUID", 12)
    assert url == "http://10.1.2.3:3000/d/abcUID?viewPanel=12"


def test_build_links_from_config_map():
    links = build_links(
        base="http://grafana.internal",
        dashboards={"gpu": "migdash"},
        panels={"gpu": 4},
    )
    assert links == [
        {"name": "gpu", "url": "http://grafana.internal/d/migdash?viewPanel=4"}
    ]


def _metrics_config(db_path: str) -> GatewayConfig:
    return GatewayConfig(
        tokens={"obs": "tok-obs"},
        service_urls={"grafana": "http://grafana.internal/api/health"},
        metrics_url="http://metrics.internal",
        db_path=db_path,
    )


def test_metrics_query_malformed_upstream_json_returns_502(tmp_path):
    config = replace(_metrics_config(str(tmp_path / "gateway.db")))
    client = TestClient(create_app(config))

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("office_gateway.metrics.httpx.AsyncClient", return_value=mock_client):
        response = client.get(
            "/v1/metrics/query",
            params={"query": "up"},
            headers={"Authorization": "Bearer tok-obs"},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "metrics upstream error"
