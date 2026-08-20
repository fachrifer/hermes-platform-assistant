from __future__ import annotations

import os
import re
from typing import Any, Awaitable, Callable, Optional

import httpx

ALLOWED_KINDS = frozenset(
    {
        "nodes",
        "pods",
        "deployments",
        "gateway",
        "httproute",
        "storageclass",
        "migpolicy",
        "configmap",
    }
)

_MIG_PROFILE_LABEL = "nvidia.com/mig.profile"
_GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"
_MIG_RESOURCE_PREFIX = "nvidia.com/mig-"
_SENSITIVE_LABEL_RE = re.compile(r"secret|token|password|kubeconfig|data", re.I)

ListFn = Callable[[str, Optional[str]], Awaitable[dict[str, Any]]]


class AdapterNotConfigured(Exception):
    pass


def _profile_from_labels(labels: dict[str, Any]) -> Optional[str]:
    profile = labels.get(_MIG_PROFILE_LABEL)
    if profile:
        return str(profile)
    product = labels.get(_GPU_PRODUCT_LABEL)
    if product:
        return str(product)
    return None


def _mig_allocatable_counts(allocatable: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, value in allocatable.items():
        if not str(key).startswith(_MIG_RESOURCE_PREFIX):
            continue
        profile_name = str(key)[len(_MIG_RESOURCE_PREFIX) :]
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount:
            counts[profile_name] = counts.get(profile_name, 0) + amount
    return counts


def count_mig_profiles(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        allocatable = (item.get("status") or {}).get("allocatable") or {}
        mig_counts = _mig_allocatable_counts(allocatable)
        if mig_counts:
            for profile, amount in mig_counts.items():
                counts[profile] = counts.get(profile, 0) + amount
            continue

        labels = (item.get("metadata") or {}).get("labels") or {}
        profile = _profile_from_labels(labels)
        if profile:
            counts[profile] = counts.get(profile, 0) + 1
    return counts


def _filter_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in labels.items()
        if not _SENSITIVE_LABEL_RE.search(str(key))
    }


def _project_status(status: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    if "phase" in status:
        projected["phase"] = status["phase"]
    if "ready" in status:
        projected["ready"] = status["ready"]
    return projected


def project_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    projected: dict[str, Any] = {
        "kind": kind,
        "name": meta.get("name", ""),
    }
    namespace = meta.get("namespace")
    if namespace:
        projected["namespace"] = namespace

    labels = _filter_labels(meta.get("labels") or {})
    if labels:
        projected["labels"] = labels

    status = _project_status(item.get("status") or {})
    if status:
        projected["status"] = status

    return projected


def sanitize_items(kind: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if kind == "configmap":
        return [
            {"kind": kind, "name": (item.get("metadata") or {}).get("name", "")}
            for item in items
        ]
    return [project_item(kind, item) for item in items]


def _kind_path(kind: str, namespace: Optional[str]) -> str:
    if kind == "nodes":
        return "/api/v1/nodes"
    if kind == "pods":
        ns = namespace or "default"
        return f"/api/v1/namespaces/{ns}/pods"
    if kind == "deployments":
        ns = namespace or "default"
        return f"/apis/apps/v1/namespaces/{ns}/deployments"
    if kind == "storageclass":
        return "/apis/storage.k8s.io/v1/storageclasses"
    if kind == "configmap":
        ns = namespace or "default"
        return f"/api/v1/namespaces/{ns}/configmaps"
    if kind == "gateway":
        if namespace:
            return f"/apis/gateway.networking.k8s.io/v1/namespaces/{namespace}/gateways"
        return "/apis/gateway.networking.k8s.io/v1/gateways"
    if kind == "httproute":
        ns = namespace or "default"
        return f"/apis/gateway.networking.k8s.io/v1/namespaces/{ns}/httproutes"
    if kind == "migpolicy":
        return "/apis/nvidia.com/v1/migpolicies"
    raise ValueError(f"unknown kind: {kind}")


async def _list_via_httpx(kind: str, namespace: Optional[str]) -> dict[str, Any]:
    api_url = os.getenv("OFFICE_K8S_API", "").strip().rstrip("/")
    token = os.getenv("OFFICE_K8S_TOKEN", "").strip()
    if not api_url or not token:
        raise AdapterNotConfigured()
    path = _kind_path(kind, namespace)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{api_url}{path}", headers=headers)
        response.raise_for_status()
        payload = response.json()
    return {"items": payload.get("items", [])}


def _list_via_kubernetes(kind: str, namespace: Optional[str]) -> dict[str, Any]:
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

    ns = namespace or "default"
    if kind == "nodes":
        api = client.CoreV1Api()
        items = [item.to_dict() for item in api.list_node().items]
    elif kind == "pods":
        api = client.CoreV1Api()
        items = [item.to_dict() for item in api.list_namespaced_pod(namespace=ns).items]
    elif kind == "deployments":
        api = client.AppsV1Api()
        items = [
            item.to_dict() for item in api.list_namespaced_deployment(namespace=ns).items
        ]
    elif kind == "storageclass":
        api = client.StorageV1Api()
        items = [item.to_dict() for item in api.list_storage_class().items]
    elif kind == "configmap":
        api = client.CoreV1Api()
        items = [item.to_dict() for item in api.list_namespaced_config_map(namespace=ns).items]
    elif kind == "gateway":
        api = client.CustomObjectsApi()
        if namespace:
            payload = api.list_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=namespace,
                plural="gateways",
            )
        else:
            payload = api.list_cluster_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                plural="gateways",
            )
        items = payload.get("items", [])
    elif kind == "httproute":
        api = client.CustomObjectsApi()
        payload = api.list_namespaced_custom_object(
            group="gateway.networking.k8s.io",
            version="v1",
            namespace=ns,
            plural="httproutes",
        )
        items = payload.get("items", [])
    elif kind == "migpolicy":
        api = client.CustomObjectsApi()
        payload = api.list_cluster_custom_object(
            group="nvidia.com",
            version="v1",
            plural="migpolicies",
        )
        items = payload.get("items", [])
    else:
        raise ValueError(f"unknown kind: {kind}")
    return {"items": items}


async def _default_list(kind: str, namespace: Optional[str]) -> dict[str, Any]:
    try:
        return _list_via_kubernetes(kind, namespace)
    except AdapterNotConfigured:
        pass
    return await _list_via_httpx(kind, namespace)


class K8sOps:
    def __init__(
        self,
        list_fn: Optional[ListFn] = None,
        service_urls: Optional[dict[str, str]] = None,
    ) -> None:
        self._list_fn = list_fn
        self._service_urls = service_urls or {}

    async def list_resources(self, kind: str, namespace: Optional[str]) -> dict[str, Any]:
        normalized = kind.lower()
        if normalized not in ALLOWED_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        list_fn = self._list_fn or _default_list
        try:
            payload = await list_fn(normalized, namespace)
        except AdapterNotConfigured as exc:
            raise AdapterNotConfigured() from exc
        items = payload.get("items", [])
        return {
            "kind": normalized,
            "items": sanitize_items(normalized, items),
        }

    async def get_mig_actual(self) -> dict[str, Any]:
        try:
            payload = await (self._list_fn or _default_list)("nodes", None)
        except AdapterNotConfigured:
            return {"actual": {}, "error": "adapter not configured"}
        except Exception:
            return {"actual": {}, "error": "adapter not configured"}
        actual = count_mig_profiles(payload.get("items", []))
        return {"actual": actual}
