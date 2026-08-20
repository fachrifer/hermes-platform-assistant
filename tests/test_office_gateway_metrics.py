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
