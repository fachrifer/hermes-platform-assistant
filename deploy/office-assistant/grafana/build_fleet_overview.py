#!/usr/bin/env python3
"""Build deploy/office-assistant/grafana/fleet-overview.json (Grafana dashboard uid fleet-overview).

The file is generated so the ~45 near-identical panels stay consistent. Run:
    python deploy/office-assistant/grafana/build_fleet_overview.py

Import: Grafana -> Dashboards -> New -> Import -> paste the JSON -> Overwrite (same uid).
Nothing here talks to Grafana.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("fleet-overview.json")
DS = {"type": "prometheus", "uid": "${datasource}"}

# Real filesystems only. node-exporter on the GPU node runs in a container and reports bind mounts
# (/etc/hosts ...), so those are filtered out and the GPU node gets its own device based query.
_FS = 'device=~"/dev/.*",fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|fuse.*|nsfs|iso9660"'
_MOUNT_NOISE = (
    'mountpoint!~"/etc/.*|/usr/.*|/opt/.*|/var/lib/.*|/var/log/.*|/run/.*|/host/.*|/snap/.*|/proc.*|/sys.*|/dev/.*"'
)
FS_REAL = f"{_FS},{_MOUNT_NOISE},instance=~\"$host\""
FS_ANY = _FS
GPU_NODE = 'instance=~"dc141f.*",device=~"/dev/(nvme|sd|mapper).*",fstype!~"tmpfs|overlay|squashfs"'

UP = {
    "type": "value",
    "options": {
        "-1": {"text": "NO DATA", "color": "yellow", "index": 2},
        "0": {"text": "DOWN", "color": "red", "index": 1},
        "1": {"text": "UP", "color": "green", "index": 0},
    },
}
STATUS_STEPS = [{"color": "red", "value": None}, {"color": "green", "value": 1}]


def steps(*pairs):
    """steps("green", ("yellow", 70), ("red", 90))"""
    out = []
    for item in pairs:
        color, value = (item, None) if isinstance(item, str) else item
        out.append({"color": color, "value": value})
    return out


def target(expr, legend=None, ref="A", instant=False, fmt=None):
    item = {"datasource": DS, "expr": expr, "refId": ref}
    if legend:
        item["legendFormat"] = legend
    if instant:
        item["instant"] = True
        item["range"] = False
    if fmt:
        item["format"] = fmt
    return item


def row(pid, title, y):
    return {"type": "row", "id": pid, "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


def stat(pid, title, targets, x, y, w, h=4, unit="short", th=None, mappings=None, desc="",
         color_mode="background", text_mode="auto", decimals=0, graph="none", no_value="N/A"):
    defaults = {
        "color": {"mode": "thresholds"},
        "decimals": decimals,
        "mappings": mappings or [],
        "noValue": no_value,
        "thresholds": {"mode": "absolute", "steps": th or steps("green")},
        "unit": unit,
    }
    return {
        "id": pid, "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "colorMode": color_mode, "graphMode": graph, "justifyMode": "auto", "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": text_mode, "wideLayout": True, "showPercentChange": False,
        },
        "targets": targets,
    }


def status_tiles(pid, title, items, x, y, w, desc):
    targets = []
    for index, (label, expr) in enumerate(items):
        targets.append(target(f"max({expr}) or vector(-1)", label, chr(65 + index)))
    return stat(pid, title, targets, x, y, w, 4, "none", STATUS_STEPS, [UP], desc,
                text_mode="value_and_name", no_value="NO DATA")


def timeseries(pid, title, targets, x, y, w, h=8, unit="short", th=None, th_style="off", desc="",
               minimum=None, maximum=None, stack=False, overrides=None):
    custom = {
        "drawStyle": "line", "lineInterpolation": "linear", "lineWidth": 2, "fillOpacity": 10,
        "gradientMode": "none", "showPoints": "never", "spanNulls": True,
        "stacking": {"group": "A", "mode": "normal" if stack else "none"},
        "thresholdsStyle": {"mode": th_style},
    }
    defaults = {
        "color": {"mode": "palette-classic"}, "custom": custom, "mappings": [], "unit": unit,
        "thresholds": {"mode": "absolute", "steps": th or steps("green")},
    }
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    return {
        "id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {
            "legend": {"calcs": ["lastNotNull", "max"], "displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc", "hideZeros": False},
        },
        "targets": targets,
    }


def bargauge(pid, title, targets, x, y, w, h=8, unit="percentunit", th=None, desc="", maximum=1):
    return {
        "id": pid, "type": "bargauge", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"}, "min": 0, "unit": unit, "decimals": 1,
            "thresholds": {"mode": "absolute", "steps": th or steps("green")}, "mappings": [],
            **({"max": maximum} if maximum is not None else {})},
            "overrides": []},
        "options": {
            "displayMode": "gradient", "orientation": "horizontal", "namePlacement": "auto",
            "showUnfilled": True, "valueMode": "color", "minVizHeight": 16, "minVizWidth": 8, "sizing": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
        "targets": targets,
    }


def panels():
    p = []
    # y0: status of hosts and services. No data is NO DATA (yellow), never a silent blank tile.
    p.append(status_tiles(10, "Hosts", [
        ("Lab-host 10.216.4.80", 'up{instance="10.216.4.80:9100"}'),
        ("GPU node dc141f0601srv", 'up{instance="dc141f0601srv.kemenkeu.go.id",job="integrations/unix"}'),
        ("Milvus PROD host 10.216.203.132", 'up{instance="10.216.203.132:9100"}'),
        ("Grafana VM 10.216.78.130", 'up{instance="10.216.78.130:9100"}'),
        ("Rancher 10.216.78.129", 'up{instance="10.216.78.129:9100"}'),
    ], 0, 0, 14, "node-exporter up per host. NO DATA = the target is not scraped at all (Rancher today)."))
    p.append(status_tiles(11, "Services", [
        ("LiteLLM", "litellm_up"),
        ("K8s (RKE2)", 'min(kube_node_status_condition{condition="Ready",status="true"})'),
        ("Milvus PROD (API)", 'up{job="milvus"}'),
        ("PostgreSQL", 'pg_up{job="postgres-lab"}'),
    ], 14, 0, 10, "1 = up. Milvus PROD is the only Milvus scraped (server=milvus-production, 10.216.203.132:9091); "
                  "there is no dev Milvus in Prometheus. K8s is the minimum Ready flag across nodes."))

    # y4: one-glance KPIs, all red when something needs a person.
    p.append(stat(20, "Targets down", [target("count(up == 0) or vector(0)", instant=True)], 0, 4, 3,
                  th=steps("green", ("red", 1)), desc="Scrape targets with up == 0."))
    p.append(stat(21, "Firing alerts", [target('count(ALERTS{alertstate="firing"}) or vector(0)', instant=True)], 3, 4, 3,
                  th=steps("green", ("red", 1)), desc="Alerts in firing state."))
    p.append(stat(22, "Pods running", [target('sum(kube_pod_status_phase{phase="Running"})', instant=True)], 6, 4, 3,
                  th=steps("blue")))
    p.append(stat(23, "Pods pending", [target('sum(kube_pod_status_phase{phase="Pending"}) or vector(0)', instant=True)], 9, 4, 3,
                  th=steps("green", ("yellow", 1))))
    p.append(stat(24, "Pod problems", [target(
        '(sum(kube_pod_status_phase{phase="Failed"}) or vector(0)) + '
        '(sum(kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull"}) or vector(0))',
        instant=True)], 12, 4, 3, th=steps("green", ("red", 1)),
        desc="Failed pods plus containers stuck in CrashLoopBackOff, ImagePullBackOff or ErrImagePull."))
    p.append(stat(25, "Deployments not ready", [target(
        "count(kube_deployment_spec_replicas > kube_deployment_status_replicas_available) or vector(0)", instant=True)],
        15, 4, 3, th=steps("green", ("red", 1)), desc="Deployments with fewer available replicas than desired."))
    p.append(stat(26, "GPUs online", [target("count(count by (gpu) (DCGM_FI_DEV_GPU_TEMP))", instant=True)], 18, 4, 3,
                  th=steps("red", ("green", 1)), desc="GPUs reporting DCGM metrics."))
    p.append(stat(27, "Fullest filesystem", [target(f"max(1 - node_filesystem_avail_bytes{{{FS_ANY}}} / "
                  f"node_filesystem_size_bytes{{{FS_ANY}}}) * 100", instant=True)], 21, 4, 3, unit="percent",
                  th=steps("green", ("yellow", 80), ("red", 90)), decimals=1,
                  desc="Highest usage across every real mount, including /boot. The old Disk panel only looked at /."))

    # Hosts
    p.append(row(100, "Hosts", 8))
    p.append(timeseries(101, "CPU usage", [target(
        '1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle",instance=~"$host"}[$__rate_interval]))', "{{instance}}")],
        0, 9, 8, unit="percentunit", th=steps("green", ("yellow", 0.7), ("red", 0.9)), th_style="dashed", minimum=0, maximum=1))
    p.append(timeseries(102, "RAM usage", [target(
        '1 - (node_memory_MemAvailable_bytes{instance=~"$host"} / node_memory_MemTotal_bytes{instance=~"$host"})', "{{instance}}")],
        8, 9, 8, unit="percentunit", th=steps("green", ("yellow", 0.7), ("red", 0.9)), th_style="dashed", minimum=0, maximum=1))
    p.append(timeseries(103, "Load per core (1m)", [target(
        'node_load1{instance=~"$host"} / on(instance) group_left count by (instance) (node_cpu_seconds_total{mode="idle",instance=~"$host"})',
        "{{instance}}")], 16, 9, 8, th=steps("green", ("yellow", 0.7), ("red", 1)), th_style="dashed", minimum=0,
        desc="Load divided by core count. Above 1.0 means runnable processes outnumber cores. Replaces the raw load panel, "
             "which needed the core counts typed into its description."))

    # Disk
    p.append(row(110, "Disk", 17))
    p.append(bargauge(111, "Fullest mounts (now)", [
        target(f"topk(12, max by (instance, mountpoint) (1 - node_filesystem_avail_bytes{{{FS_REAL}}} / "
               f"node_filesystem_size_bytes{{{FS_REAL}}}))", "{{instance}} {{mountpoint}}", "A", instant=True),
        target(f"max by (instance, device) (1 - node_filesystem_avail_bytes{{{GPU_NODE}}} / "
               f"node_filesystem_size_bytes{{{GPU_NODE}}})", "{{instance}} {{device}}", "B", instant=True),
    ], 0, 18, 12, th=steps("green", ("yellow", 0.8), ("red", 0.9)),
        desc="Every real mount per host, fullest first. /boot is included on purpose: a full /boot blocks kernel updates."))
    p.append(timeseries(112, "Disk usage over time (all mounts)", [
        target(f"max by (instance, mountpoint) (1 - node_filesystem_avail_bytes{{{FS_REAL}}} / "
               f"node_filesystem_size_bytes{{{FS_REAL}}})", "{{instance}} {{mountpoint}}", "A"),
        target(f"max by (instance, device) (1 - node_filesystem_avail_bytes{{{GPU_NODE}}} / "
               f"node_filesystem_size_bytes{{{GPU_NODE}}})", "{{instance}} {{device}}", "B"),
    ], 12, 18, 12, unit="percentunit", th=steps("green", ("yellow", 0.8), ("red", 0.9)), th_style="dashed", minimum=0, maximum=1))

    # GPU
    p.append(row(120, "GPU (H200)", 26))
    p.append(timeseries(121, "VRAM used %", [target(
        "max by (gpu) (DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE))", "GPU {{gpu}}")],
        0, 27, 6, unit="percentunit", th=steps("green", ("yellow", 0.85), ("red", 0.95)), th_style="dashed", minimum=0, maximum=1,
        desc="Replaces the 0/1 active-idle panel: it shows how full each GPU actually is."))
    p.append(timeseries(122, "VRAM used / free", [
        target("max by (gpu) (DCGM_FI_DEV_FB_USED) * 1024 * 1024", "GPU {{gpu}} used", "A"),
        target("max by (gpu) (DCGM_FI_DEV_FB_FREE) * 1024 * 1024", "GPU {{gpu}} free", "B"),
    ], 6, 27, 6, unit="bytes", minimum=0, desc="DCGM reports MiB; converted to bytes. H200 is about 141 GB."))
    p.append(timeseries(123, "GPU temperature", [target("max by (gpu) (DCGM_FI_DEV_GPU_TEMP)", "GPU {{gpu}}")],
                        12, 27, 6, unit="celsius", th=steps("green", ("yellow", 75), ("red", 85)), th_style="dashed", minimum=0))
    p.append(timeseries(124, "GPU power", [target("max by (gpu) (DCGM_FI_DEV_POWER_USAGE)", "GPU {{gpu}}")],
                        18, 27, 6, unit="watt", minimum=0, desc="Split from temperature: the two shared one axis before."))

    # Kubernetes
    p.append(row(130, "Kubernetes", 35))
    p.append(timeseries(131, "Pods by phase", [target("sum by (phase) (kube_pod_status_phase)", "{{phase}}")],
                        0, 36, 12, h=7, minimum=0))
    p.append(bargauge(132, "Container restarts (last 1h) by namespace", [target(
        "topk(10, sum by (namespace) (increase(kube_pod_container_status_restarts_total[1h])))", "{{namespace}}", instant=True)],
        12, 36, 12, h=7, unit="short", maximum=10, th=steps("green", ("yellow", 1), ("red", 5)),
        desc="Namespaces whose containers restarted in the last hour."))

    # Traffic
    p.append(row(140, "Traffic (Traefik)", 43))
    p.append(timeseries(141, "Requests per second by path", [target(
        'topk(8, sum by (url_path) (rate(traces_spanmetrics_calls_total{job="traefik",url_path!~"/ping|/llm/health.*"}[5m])))',
        "{{url_path}}")], 0, 44, 8, unit="reqps", minimum=0,
        desc="Real requests per second reaching Traefik, health checks filtered out. High is normal traffic, not a fault."))
    p.append(timeseries(142, "HTTP error rate", [
        target('sum(rate(traces_spanmetrics_calls_total{job="traefik",http_response_status_code=~"4..|5.."}[5m])) / '
               'sum(rate(traces_spanmetrics_calls_total{job="traefik"}[5m])) * 100', "Total error % (4xx+5xx)", "A"),
        target('sum(rate(traces_spanmetrics_calls_total{job="traefik",http_response_status_code=~"5.."}[5m])) / '
               'sum(rate(traces_spanmetrics_calls_total{job="traefik"}[5m])) * 100', "5xx (server) %", "B"),
    ], 8, 44, 8, unit="percent", th=steps("green", ("yellow", 5), ("red", 20)), th_style="dashed", minimum=0, maximum=100,
        desc="4xx includes scanner 404s and is normal. 5xx is our own errors and should stay at 0%."))
    p.append(timeseries(143, "Response latency", [
        target('sum(rate(traces_spanmetrics_duration_milliseconds_sum{job="traefik",url_path=~"/llm/v1/chat.*"}[5m])) / '
               'sum(rate(traces_spanmetrics_duration_milliseconds_count{job="traefik",url_path=~"/llm/v1/chat.*"}[5m]))',
               "LLM chat (avg)", "A"),
        target('histogram_quantile(0.50, sum by (le) (rate(traces_spanmetrics_duration_milliseconds_bucket'
               '{job="traefik",url_path!~"/llm/v1/chat.*|/ping"}[5m])))', "Non-LLM (p50)", "B"),
    ], 16, 44, 8, unit="ms", minimum=0,
        desc="LLM chat streams, so thousands of ms is normal. Non-LLM p50 should stay under about 100 ms."))

    # Data services
    p.append(row(150, "Data services", 52))
    p.append(stat(151, "Milvus collections", [target("sum(milvus_datacoord_collection_num)", instant=True)], 0, 53, 4,
                  desc="Collections in Milvus PROD."))
    p.append(stat(152, "Milvus stored rows", [target("sum(milvus_datacoord_stored_rows_num)", instant=True)], 4, 53, 5,
                  desc="Total vectors across all collections."))
    p.append(stat(153, "MinIO bucket size", [target('sum(minio_bucket_size_bytes{instance="10.216.203.132:9100"})', instant=True)],
                  9, 53, 5, unit="bytes", decimals=2, th=steps("blue"), desc="Milvus object storage (files-server-vector-database)."))
    p.append(stat(154, "MinIO objects", [target('sum(minio_bucket_objects{instance="10.216.203.132:9100"})', instant=True)],
                  14, 53, 5, th=steps("blue"), desc="Objects in the MinIO bucket."))
    p.append(stat(155, "Milvus disk (/)", [target(
        '(1 - node_filesystem_avail_bytes{instance="10.216.203.132:9100",mountpoint="/"} / '
        'node_filesystem_size_bytes{instance="10.216.203.132:9100",mountpoint="/"}) * 100', instant=True)],
        19, 53, 5, unit="percent", decimals=1, graph="area", th=steps("green", ("yellow", 70), ("red", 90)),
        desc="Root disk of the Milvus PROD node. Above 90% needs more capacity."))
    p.append(stat(156, "PostgreSQL connections", [target('sum(pg_stat_activity_count{job="postgres-lab"})', instant=True)],
                  0, 57, 6, h=6, th=steps("green", ("yellow", 80), ("red", 90)), graph="area",
                  desc="Active connections. max_connections is 100; above 80 needs attention."))
    table = {
        "id": 157, "type": "table", "title": "PostgreSQL databases", "datasource": DS,
        "description": "Every database on the lab-host PostgreSQL: size and connections.",
        "gridPos": {"h": 6, "w": 18, "x": 6, "y": 57},
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                     "mappings": [], "thresholds": {"mode": "absolute", "steps": steps("green")},
                                     "unit": "decbytes"},
                        "overrides": [{"matcher": {"id": "byName", "options": "Connections"},
                                       "properties": [{"id": "unit", "value": "short"}]}]},
        "options": {"cellHeight": "sm", "showHeader": True, "footer": {"show": False, "reducer": ["sum"], "fields": "",
                                                                       "countRows": False},
                    "sortBy": [{"displayName": "Size", "desc": True}]},
        "targets": [
            target('pg_database_size_bytes{job="postgres-lab"}', ref="A", instant=True, fmt="table"),
            target('sum by (datname) (pg_stat_activity_count{job="postgres-lab"})', ref="B", instant=True, fmt="table"),
        ],
        "transformations": [
            {"id": "joinByField", "options": {"byField": "datname", "mode": "outer"}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "__name__": True, "instance": True,
                                  "job": True, "service": True},
                "indexByName": {"datname": 0, "Value #A": 1, "Value #B": 2},
                "renameByName": {"Value #A": "Size", "Value #B": "Connections", "datname": "Database"}}},
        ],
    }
    p.append(table)
    return p


def dashboard():
    return {
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"}]},
        "description": "Platform overview: hosts, services, Kubernetes, GPU, traffic and data services. "
                       "Red or yellow tiles at the top mean something needs a person.",
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": 33,
        "links": [
            {"title": "Milvus & Server Monitor", "type": "link", "icon": "dashboard", "targetBlank": False,
             "url": "/d/de68d706-4a90-4ad8-b338-fb5d94b09af0"},
            {"title": "Insightface prod", "type": "link", "icon": "dashboard", "targetBlank": False,
             "url": "/d/Insightface-prod-v2"},
        ],
        "liveNow": False, "panels": panels(), "preload": False, "refresh": "1m", "schemaVersion": 41,
        "tags": ["fleet", "overview", "monitoring"],
        "templating": {"list": [
            {"name": "datasource", "label": "Datasource", "type": "datasource", "query": "prometheus", "refresh": 1,
             "hide": 0, "includeAll": False, "multi": False, "options": [], "regex": "", "skipUrlSync": False,
             "current": {"selected": False, "text": "prometheus", "value": "afdxlp9p1t14wf"}},
            {"name": "host", "label": "Host", "type": "query", "datasource": DS, "refresh": 2, "sort": 1, "hide": 0,
             "definition": "label_values(node_uname_info, instance)",
             "query": {"qryType": 1, "query": "label_values(node_uname_info, instance)",
                       "refId": "PrometheusVariableQueryEditor-VariableQuery"},
             "includeAll": True, "allValue": ".*", "multi": True, "options": [], "regex": "", "skipUrlSync": False,
             "current": {"selected": True, "text": ["All"], "value": ["$__all"]}},
        ]},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m", "1h"]},
        "timezone": "browser", "title": "Fleet Overview — All Hosts & Services", "uid": "fleet-overview", "version": 46,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(panels())} panels)")
