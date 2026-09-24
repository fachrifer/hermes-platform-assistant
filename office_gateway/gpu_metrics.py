"""GPU metrics via DCGM exporter through the Kubernetes API service proxy."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from office_gateway.k8s_ops import AdapterNotConfigured

_DEFAULT_SERVICE = "nvidia-dcgm-exporter:9400"
_DEFAULT_NAMESPACE = "gpu-operator"
_DEFAULT_PATH = "/metrics"

_METRIC_LINE = re.compile(r"^(\w+)\{([^}]+)\}\s+([\d.eE+-]+)")
_GPU_LABEL = re.compile(r'gpu="(\d+)"')

_FIELD_MAP = {
    "DCGM_FI_DEV_GPU_UTIL": ("util_pct", lambda v: round(v)),
    "DCGM_FI_DEV_FB_USED": ("mem_used_mib", lambda v: round(v)),
    "DCGM_FI_DEV_FB_TOTAL": ("mem_total_mib", lambda v: round(v)),
    "DCGM_FI_DEV_GPU_TEMP": ("temp_c", lambda v: round(v)),
    "DCGM_FI_DEV_POWER_USAGE": ("power_w", lambda v: round(v, 1)),
    "DCGM_FI_DEV_SM_CLOCK": ("sm_clock_mhz", lambda v: round(v)),
    "DCGM_FI_DEV_MEM_CLOCK": ("mem_clock_mhz", lambda v: round(v)),
    # DCGM PCIe throughput is KB/s.
    "DCGM_FI_DEV_PCIE_TX_THROUGHPUT": ("pcie_tx_mb_s", lambda v: round(v / 1024)),
    "DCGM_FI_DEV_PCIE_RX_THROUGHPUT": ("pcie_rx_mb_s", lambda v: round(v / 1024)),
    "DCGM_FI_DEV_ENC_UTIL": ("enc_util_pct", lambda v: round(v)),
    "DCGM_FI_DEV_DEC_UTIL": ("dec_util_pct", lambda v: round(v)),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dcgm_proxy_path(
    namespace: str | None = None,
    service: str | None = None,
    metrics_path: str | None = None,
) -> str:
    ns = (namespace or os.getenv("OFFICE_DCGM_NAMESPACE", "")).strip() or _DEFAULT_NAMESPACE
    svc = (service or os.getenv("OFFICE_DCGM_SERVICE", "")).strip() or _DEFAULT_SERVICE
    path = (metrics_path or os.getenv("OFFICE_DCGM_PATH", "")).strip() or _DEFAULT_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return f"/api/v1/namespaces/{ns}/services/{svc}/proxy{path}"


def parse_dcgm_text(text: str, observed_at: str | None = None) -> dict[str, Any]:
    gpus: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC_LINE.match(line)
        if not match:
            continue
        metric, labels, value_str = match.groups()
        gpu_match = _GPU_LABEL.search(labels)
        if not gpu_match or metric not in _FIELD_MAP:
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue
        gpu_id = gpu_match.group(1)
        entry = gpus.setdefault(gpu_id, {"id": int(gpu_id)})
        field, convert = _FIELD_MAP[metric]
        entry[field] = convert(value)

    for gpu in gpus.values():
        used = gpu.get("mem_used_mib")
        total = gpu.get("mem_total_mib")
        if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
            gpu["mem_pct"] = round(used / total * 100)

    return {
        "gpus": sorted(gpus.values(), key=lambda item: item["id"]),
        "count": len(gpus),
        "observed_at": observed_at or _now(),
    }


def gpu_metrics_error(message: str) -> dict[str, Any]:
    return {
        "gpus": [],
        "count": 0,
        "error": message[:300],
        "observed_at": _now(),
    }


def _dcgm_target() -> tuple[str, str, str]:
    ns = os.getenv("OFFICE_DCGM_NAMESPACE", "").strip() or _DEFAULT_NAMESPACE
    svc = os.getenv("OFFICE_DCGM_SERVICE", "").strip() or _DEFAULT_SERVICE
    path = os.getenv("OFFICE_DCGM_PATH", "").strip() or _DEFAULT_PATH
    path = path.lstrip("/")
    return ns, svc, path


def _load_kubernetes() -> Any:
    try:
        from kubernetes import client, config
    except ImportError as exc:
        raise AdapterNotConfigured() from exc

    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            kubeconfig = (
                os.getenv("KUBECONFIG", "").strip()
                or os.getenv("OFFICE_KUBECONFIG", "").strip()
            )
            if kubeconfig:
                config.load_kube_config(config_file=kubeconfig)
            else:
                config.load_kube_config()
        except config.ConfigException as exc:
            raise AdapterNotConfigured() from exc
    return client


def _scrape_via_kubernetes() -> str:
    client = _load_kubernetes()
    namespace, name, path = _dcgm_target()
    core = client.CoreV1Api()
    result = core.connect_get_namespaced_service_proxy_with_path(
        name, namespace, path
    )
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    return str(result)


async def _scrape_via_httpx() -> str:
    api_url = os.getenv("OFFICE_K8S_API", "").strip().rstrip("/")
    token = os.getenv("OFFICE_K8S_TOKEN", "").strip()
    if not api_url or not token:
        raise AdapterNotConfigured()
    url = f"{api_url}{dcgm_proxy_path()}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"DCGM scrape failed ({response.status_code}): {response.text[:300]}"
            )
        return response.text


async def scrape_dcgm_text() -> str:
    try:
        return await asyncio.to_thread(_scrape_via_kubernetes)
    except AdapterNotConfigured:
        pass
    return await _scrape_via_httpx()
