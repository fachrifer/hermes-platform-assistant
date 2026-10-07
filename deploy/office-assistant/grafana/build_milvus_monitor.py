#!/usr/bin/env python3
"""Build deploy/office-assistant/grafana/milvus-monitor.json (uid de68d706-..., "Milvus & Server Monitor").

Run:  python deploy/office-assistant/grafana/build_milvus_monitor.py
Import: Grafana -> Dashboards -> New -> Import -> paste -> Overwrite (same uid). Nothing here talks to Grafana.

Everything is scoped by the `server` label Prometheus already puts on the Milvus targets
(server="milvus-production"), so a future dev Milvus is a dropdown entry, not a copy of the dashboard.
"""
from __future__ import annotations

import json
from pathlib import Path

from build_fleet_overview import (  # noqa: E402  (same directory)
    DS, STATUS_STEPS, UP, _FS, _MOUNT_NOISE, bargauge, row, stat, steps, target, timeseries,
)

OUT = Path(__file__).with_name("milvus-monitor.json")
UID = "de68d706-4a90-4ad8-b338-fb5d94b09af0"

S = 'server="$server"'
MILVUS = f'job="milvus",{S}'
FS_REAL = f"{_FS},{_MOUNT_NOISE},{S}"


def gauge(pid, title, expr, x, y, w, h=5, th=None, desc="", unit="percent", maximum=100):
    return {
        "id": pid, "type": "gauge", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"}, "decimals": 1, "mappings": [], "min": 0, "max": maximum, "unit": unit,
            "thresholds": {"mode": "absolute", "steps": th or steps("green", ("orange", 90), ("red", 95))}},
            "overrides": []},
        "options": {"minVizHeight": 75, "minVizWidth": 75, "orientation": "auto", "showThresholdLabels": False,
                    "showThresholdMarkers": True, "sizing": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "targets": [target(expr, instant=True)],
    }


def panels():
    p = []
    # Status: the questions you ask first. Red = needs a person.
    p.append(row(14, "Status - $server", 0))
    p.append(stat(6, "Milvus status", [target(f"max(up{{{MILVUS}}}) or vector(-1)", instant=True)], 0, 1, 3, 4, "none",
                  STATUS_STEPS, [UP], "Scrape of the Milvus metrics endpoint. NO DATA = not scraped at all.",
                  no_value="NO DATA"))
    p.append(stat(2, "Server uptime", [target(f"time() - max(node_boot_time_seconds{{{S}}})", instant=True)], 0 + 3, 1, 3, 4,
                  "dtdhms", steps("green"), desc="Time since the host booted."))
    p.append(stat(7, "Query nodes", [target(f"sum(milvus_querycoord_querynode_num{{{S}}})", instant=True)], 6, 1, 3, 4,
                  th=steps("red", ("green", 1)), desc="Active query nodes. 0 means nothing can be searched."))
    p.append(stat(8, "Collections loaded / total", [
        target(f"sum(milvus_querycoord_collection_num{{{S}}})", "loaded", "A", instant=True),
        target(f"sum(milvus_rootcoord_collection_num{{{S}}})", "total", "B", instant=True)],
        9, 1, 3, 4, th=steps("blue"), text_mode="value_and_name", color_mode="value",
        desc="Loaded = collections held in memory and searchable. Total = all collections that exist. "
             "A collection that exists but is not loaded cannot be searched until it is loaded."))
    p.append(stat(9, "Searchable vectors", [target(f"sum(milvus_querynode_entity_num{{{S}}})", instant=True)], 12, 1, 3, 4,
                  th=steps("blue"), desc="Entities (vectors) in memory on the query nodes, sealed and growing."))
    p.append(stat(10, "Segments loaded", [target(f"sum(milvus_querynode_segment_num{{{S}}})", instant=True)], 15, 1, 3, 4,
                  th=steps("blue"), desc="Segments loaded by the query nodes."))
    p.append(stat(22, "Failed requests (1h)", [target(
        f'sum(increase(milvus_proxy_req_count{{{MILVUS},status="fail"}}[1h])) / '
        f'sum(increase(milvus_proxy_req_count{{{MILVUS},status="total"}}[1h])) * 100', instant=True)], 18, 1, 3, 4,
        unit="percent", decimals=2, th=steps("green", ("yellow", 1), ("red", 5)), no_value="idle",
        desc="Share of API requests that failed in the last hour. Idle when there were no requests."))
    p.append(stat(23, "Search latency p95 (5m)", [target(
        f'histogram_quantile(0.95, sum by (le) (rate(milvus_proxy_sq_latency_bucket{{{MILVUS},query_type="search"}}[5m])))',
        instant=True)], 21, 1, 3, 4, unit="ms", decimals=0, th=steps("green", ("yellow", 100), ("red", 500)),
        no_value="idle", desc="95th percentile search latency. Idle when nobody searched in the last 5 minutes."))

    # Server
    p.append(row(30, "Server (host)", 5))
    p.append(gauge(3, "CPU usage",
                   f'100 - avg(rate(node_cpu_seconds_total{{mode="idle",{S}}}[5m])) * 100', 0, 6, 4,
                   desc="Whole host, averaged over all cores."))
    p.append(gauge(4, "RAM usage",
                   f"100 * (1 - max(node_memory_MemAvailable_bytes{{{S}}}) / max(node_memory_MemTotal_bytes{{{S}}}))", 4, 6, 4))
    p.append(gauge(5, "Root disk (/)",
                   f'100 * (1 - max(node_filesystem_avail_bytes{{mountpoint="/",{S}}}) / '
                   f'max(node_filesystem_size_bytes{{mountpoint="/",{S}}}))', 8, 6, 4))
    p.append(gauge(31, "Fullest mount",
                   f"100 * max(1 - node_filesystem_avail_bytes{{{FS_REAL}}} / node_filesystem_size_bytes{{{FS_REAL}}})",
                   12, 6, 4, th=steps("green", ("yellow", 80), ("red", 90)),
                   desc="Highest usage across every real mount, including /boot."))
    p.append(stat(21, "Root disk free", [target(f'max(node_filesystem_avail_bytes{{mountpoint="/",{S}}})', instant=True)],
                  16, 6, 4, 5, "decbytes", steps("red", ("yellow", 32e9), ("green", 63e9)), decimals=1,
                  color_mode="value", desc="Free space on /. Red under about 10% of the disk, yellow under 20%."))
    p.append(stat(32, "Load per core (1m)", [target(
        f'max(node_load1{{{S}}}) / count(node_cpu_seconds_total{{mode="idle",{S}}})', instant=True)], 20, 6, 4, 5,
        decimals=2, th=steps("green", ("yellow", 0.7), ("red", 1)), color_mode="value",
        desc="Load divided by core count. Above 1.0 means more runnable processes than cores."))
    p.append(timeseries(33, "Host CPU and RAM", [
        target(f'100 - avg(rate(node_cpu_seconds_total{{mode="idle",{S}}}[$__rate_interval])) * 100', "CPU %", "A"),
        target(f"100 * (1 - max(node_memory_MemAvailable_bytes{{{S}}}) / max(node_memory_MemTotal_bytes{{{S}}}))", "RAM %", "B"),
    ], 0, 11, 12, h=7, unit="percent", th=steps("green", ("yellow", 70), ("red", 90)), th_style="dashed", minimum=0, maximum=100))
    p.append(timeseries(34, "Host network and disk I/O", [
        target(f'sum(rate(node_network_receive_bytes_total{{{S},device!~"lo|veth.*|docker.*|br-.*"}}[$__rate_interval]))',
               "network in", "A"),
        target(f'sum(rate(node_network_transmit_bytes_total{{{S},device!~"lo|veth.*|docker.*|br-.*"}}[$__rate_interval]))',
               "network out", "B"),
        target(f"sum(rate(node_disk_read_bytes_total{{{S}}}[$__rate_interval]))", "disk read", "C"),
        target(f"sum(rate(node_disk_written_bytes_total{{{S}}}[$__rate_interval]))", "disk write", "D"),
    ], 12, 11, 12, h=7, unit="Bps", minimum=0))

    # API
    p.append(row(40, "API activity", 18))
    p.append(timeseries(17, "Requests per second by function", [target(
        f'sum by (function_name) (rate(milvus_proxy_req_count{{{MILVUS},status="total"}}[$__rate_interval]))',
        "{{function_name}}")], 0, 19, 9, minimum=0, unit="reqps",
        desc="Replaces the old panel that plotted success and total as separate lines for every function."))
    p.append(timeseries(41, "Failed or abandoned per second", [target(
        f'sum by (function_name, status) (rate(milvus_proxy_req_count{{{MILVUS},status=~"fail|abandon"}}[$__rate_interval]))',
        "{{function_name}} {{status}}")], 9, 19, 6, minimum=0, unit="reqps",
        desc="Should stay empty. A line here means requests are failing."))
    p.append(timeseries(42, "Search and query latency", [
        target(f'histogram_quantile(0.50, sum by (le) (rate(milvus_proxy_sq_latency_bucket{{{MILVUS},query_type="search"}}[$__rate_interval])))',
               "search p50", "A"),
        target(f'histogram_quantile(0.95, sum by (le) (rate(milvus_proxy_sq_latency_bucket{{{MILVUS},query_type="search"}}[$__rate_interval])))',
               "search p95", "B"),
        target(f'histogram_quantile(0.99, sum by (le) (rate(milvus_proxy_sq_latency_bucket{{{MILVUS},query_type="search"}}[$__rate_interval])))',
               "search p99", "C"),
        target(f'histogram_quantile(0.95, sum by (le) (rate(milvus_proxy_sq_latency_bucket{{{MILVUS},query_type="query"}}[$__rate_interval])))',
               "query p95", "D"),
    ], 15, 19, 9, unit="ms", minimum=0, th=steps("green", ("yellow", 100), ("red", 500)), th_style="dashed",
        desc="Gaps mean no requests in that window, not a failure."))

    # Data
    p.append(row(50, "Data", 27))
    p.append(bargauge(51, "Searchable vectors by database", [target(
        f"sum by (db_name, segment_state) (milvus_querynode_entity_num{{{S}}})", "{{db_name}} ({{segment_state}})", instant=True)],
        0, 28, 8, h=7, unit="short", th=steps("blue"),
        desc="Sealed = indexed and settled. Growing = recent inserts not yet sealed."))
    p.append(timeseries(52, "Searches per second by collection", [target(
        f'sum by (db_name, collection_name) (rate(milvus_proxy_sq_latency_count{{{MILVUS}}}[$__rate_interval]))',
        "{{db_name}}/{{collection_name}}")], 8, 28, 8, h=7, minimum=0, unit="reqps"))
    p.append(timeseries(53, "Vectors inserted per second by collection", [target(
        f'sum by (db_name, collection_name) (rate(milvus_proxy_insert_vectors_count{{{MILVUS}}}[$__rate_interval]))',
        "{{db_name}}/{{collection_name}}")], 16, 28, 8, h=7, minimum=0))

    # Process and storage
    p.append(row(60, "Milvus process and storage", 35))
    p.append(timeseries(20, "Milvus RAM (RSS)", [target(f"process_resident_memory_bytes{{{MILVUS}}}", "RAM")],
                        0, 36, 6, h=7, unit="bytes", minimum=0))
    p.append(timeseries(19, "Milvus CPU", [target(f"rate(process_cpu_seconds_total{{{MILVUS}}}[$__rate_interval]) * 100", "CPU")],
                        6, 36, 6, h=7, unit="percent", minimum=0,
                        desc="Percent of ONE core: 100 means one full core, 1600 would be all 16."))
    p.append(timeseries(61, "MinIO bucket size", [target(f"sum by (bucket) (minio_bucket_size_bytes{{{S}}})", "{{bucket}}")],
                        12, 36, 6, h=7, unit="bytes", minimum=0,
                        desc="Object storage used by Milvus. It lives on the root disk, so watch Root disk free above."))
    p.append(timeseries(62, "MinIO objects", [target(f"sum by (bucket) (minio_bucket_objects{{{S}}})", "{{bucket}}")],
                        18, 36, 6, h=7, minimum=0))
    return p


def dashboard():
    return {
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"}]},
        "description": "Milvus (API, query nodes, storage) and the host it runs on. Pick the server in the dropdown.",
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": 28,
        "links": [{"title": "Fleet Overview", "type": "link", "icon": "dashboard", "targetBlank": False,
                   "url": "/d/fleet-overview"}],
        "liveNow": False, "panels": panels(), "preload": False, "refresh": "30s", "schemaVersion": 41,
        "tags": ["milvus", "infrastructure", "production"],
        "templating": {"list": [
            {"name": "datasource", "label": "Datasource", "type": "datasource", "query": "prometheus", "refresh": 1,
             "hide": 0, "includeAll": False, "multi": False, "options": [], "regex": "", "skipUrlSync": False,
             "current": {"selected": False, "text": "prometheus", "value": "afdxlp9p1t14wf"}},
            {"name": "server", "label": "Server", "type": "query", "datasource": DS, "refresh": 2, "sort": 1, "hide": 0,
             "definition": 'label_values(up{job="milvus"}, server)',
             "query": {"qryType": 1, "query": 'label_values(up{job="milvus"}, server)',
                       "refId": "PrometheusVariableQueryEditor-VariableQuery"},
             "includeAll": False, "multi": False, "options": [], "regex": "", "skipUrlSync": False,
             "current": {"selected": True, "text": "milvus-production", "value": "milvus-production"}},
        ]},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
        "timezone": "browser", "title": "Milvus & Server Monitor", "uid": UID, "version": 7,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(panels())} panels)")
