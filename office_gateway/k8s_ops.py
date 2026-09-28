from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Awaitable, Callable, Optional

import httpx

# kind → (api path template, namespaced). {ns} is replaced when namespace is set.
# Traefik CRDs are traefik.io/v1alpha1 (not v1 / ingroutroutes).
_KIND_MAP: dict[str, tuple[str, bool]] = {
    "nodes": ("/api/v1/nodes", False),
    "namespaces": ("/api/v1/namespaces", False),
    "pods": ("/api/v1/namespaces/{ns}/pods", True),
    "services": ("/api/v1/namespaces/{ns}/services", True),
    "endpoints": ("/api/v1/namespaces/{ns}/endpoints", True),
    "pvc": ("/api/v1/namespaces/{ns}/persistentvolumeclaims", True),
    "pv": ("/api/v1/persistentvolumes", False),
    "events": ("/api/v1/namespaces/{ns}/events", True),
    "secrets": ("/api/v1/namespaces/{ns}/secrets", True),
    "configmap": ("/api/v1/namespaces/{ns}/configmaps", True),
    "serviceaccounts": ("/api/v1/namespaces/{ns}/serviceaccounts", True),
    "resourcequotas": ("/api/v1/namespaces/{ns}/resourcequotas", True),
    "limitranges": ("/api/v1/namespaces/{ns}/limitranges", True),
    "replicationcontrollers": ("/api/v1/namespaces/{ns}/replicationcontrollers", True),
    "deployments": ("/apis/apps/v1/namespaces/{ns}/deployments", True),
    "statefulsets": ("/apis/apps/v1/namespaces/{ns}/statefulsets", True),
    "daemonsets": ("/apis/apps/v1/namespaces/{ns}/daemonsets", True),
    "replicasets": ("/apis/apps/v1/namespaces/{ns}/replicasets", True),
    "ingress": ("/apis/networking.k8s.io/v1/namespaces/{ns}/ingresses", True),
    "networkpolicies": (
        "/apis/networking.k8s.io/v1/namespaces/{ns}/networkpolicies",
        True,
    ),
    "gateway": ("/apis/gateway.networking.k8s.io/v1/gateways", False),
    "httproute": ("/apis/gateway.networking.k8s.io/v1/namespaces/{ns}/httproutes", True),
    "storageclass": ("/apis/storage.k8s.io/v1/storageclasses", False),
    "csidriver": ("/apis/storage.k8s.io/v1/csidrivers", False),
    "csinode": ("/apis/storage.k8s.io/v1/csinodes", False),
    "volumeattachments": ("/apis/storage.k8s.io/v1/volumeattachments", False),
    "ingressroute": (
        "/apis/traefik.io/v1alpha1/namespaces/{ns}/ingressroutes",
        True,
    ),
    "middleware": ("/apis/traefik.io/v1alpha1/namespaces/{ns}/middlewares", True),
    "traefikservice": (
        "/apis/traefik.io/v1alpha1/namespaces/{ns}/traefikservices",
        True,
    ),
    "migpolicy": ("/apis/nvidia.com/v1/migpolicies", False),
    "jobs": ("/apis/batch/v1/namespaces/{ns}/jobs", True),
    "cronjobs": ("/apis/batch/v1/namespaces/{ns}/cronjobs", True),
    "roles": ("/apis/rbac.authorization.k8s.io/v1/namespaces/{ns}/roles", True),
    "clusterroles": ("/apis/rbac.authorization.k8s.io/v1/clusterroles", False),
    "rolebindings": (
        "/apis/rbac.authorization.k8s.io/v1/namespaces/{ns}/rolebindings",
        True,
    ),
}

_KIND_ALIASES = {
    "persistentvolumeclaim": "pvc",
    "persistentvolume": "pv",
}

ALLOWED_KINDS = frozenset(_KIND_MAP) | frozenset(_KIND_ALIASES)

_CUSTOM_OBJECTS: dict[str, tuple[str, str, str, bool]] = {
    "gateway": ("gateway.networking.k8s.io", "v1", "gateways", False),
    "httproute": ("gateway.networking.k8s.io", "v1", "httproutes", True),
    "ingressroute": ("traefik.io", "v1alpha1", "ingressroutes", True),
    "middleware": ("traefik.io", "v1alpha1", "middlewares", True),
    "traefikservice": ("traefik.io", "v1alpha1", "traefikservices", True),
    "migpolicy": ("nvidia.com", "v1", "migpolicies", False),
}

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


def _canonical_kind(kind: str) -> str:
    return _KIND_ALIASES.get(kind, kind)


def _pick(data: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    """Read a field from REST JSON (camelCase) or kubernetes-client to_dict() (snake_case)."""
    value = data.get(camel)
    if value is None:
        value = data.get(snake)
    return default if value is None else value


def _node_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    labels = meta.get("labels") or {}
    conditions = {c.get("type"): c.get("status") for c in status.get("conditions") or []}
    capacity = status.get("capacity") or {}
    allocatable = status.get("allocatable") or {}
    info = _pick(status, "nodeInfo", "node_info", {})
    return {
        "kind": "nodes",
        "name": meta.get("name", ""),
        "roles": sorted(k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")),
        "ready": conditions.get("Ready", "Unknown"),
        "pressure": sorted(t for t, s in conditions.items() if t.endswith("Pressure") and s == "True"),
        "unschedulable": bool(spec.get("unschedulable")),
        "internalIP": next(
            (a.get("address") for a in status.get("addresses") or [] if a.get("type") == "InternalIP"), ""
        ),
        "kubelet": _pick(info, "kubeletVersion", "kubelet_version", ""),
        "cpu": capacity.get("cpu", ""),
        "memory": capacity.get("memory", ""),
        "gpu": {k: v for k, v in allocatable.items() if k.startswith("nvidia.com/")},
        "gpuProduct": labels.get("nvidia.com/gpu.product", ""),
        "migConfig": labels.get("nvidia.com/mig.config", ""),
        "taints": [f"{t.get('key')}:{t.get('effect')}" for t in spec.get("taints") or []],
    }


def _pod_summary(item: dict[str, Any]) -> dict[str, Any]:
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    volumes: list[str] = []
    for volume in spec.get("volumes", []):
        if "persistentVolumeClaim" in volume:
            volumes.append(volume["persistentVolumeClaim"].get("claimName", ""))
        elif "emptyDir" in volume:
            volumes.append(f"emptyDir:{volume.get('name', '')}")
        elif "hostPath" in volume:
            volumes.append(f"hostPath:{(volume.get('hostPath') or {}).get('path', '')}")
    containers: list[dict[str, Any]] = []
    for container in spec.get("containers", []):
        entry: dict[str, Any] = {
            "name": container.get("name", ""),
            "image": container.get("image", ""),
        }
        resources = container.get("resources") or {}
        if resources.get("requests") or resources.get("limits"):
            entry["resources"] = resources
        containers.append(entry)
    meta = item.get("metadata") or {}
    return {
        "kind": "pods",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", "default"),
        "phase": status.get("phase", "Unknown"),
        "node": _pick(spec, "nodeName", "node_name", ""),
        "podIP": _pick(status, "podIP", "pod_ip", ""),
        "pvcs": [name for name in volumes if name],
        "containers": containers,
        "restartCount": sum(
            _pick(container, "restartCount", "restart_count", 0)
            for container in _pick(status, "containerStatuses", "container_statuses", [])
        ),
    }


def _secret_meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "kind": "secrets",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", "default"),
        "type": item.get("type", ""),
        "creationTimestamp": meta.get("creationTimestamp", ""),
    }


def _pvc_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    capacity = (status.get("capacity") or {}).get("storage", "")
    return {
        "kind": "pvc",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", "default"),
        "phase": status.get("phase", ""),
        "capacity": capacity,
        "storageClass": spec.get("storageClassName", ""),
        "accessModes": spec.get("accessModes", []),
    }


def _service_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    ports = [
        {
            "port": port.get("port"),
            "targetPort": port.get("targetPort"),
            "protocol": port.get("protocol", "TCP"),
        }
        for port in spec.get("ports", [])
    ]
    annotations = {
        key: value
        for key, value in (meta.get("annotations") or {}).items()
        if "traefik" in str(key).lower()
    }
    return {
        "kind": "service",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", "default"),
        "type": spec.get("type", "ClusterIP"),
        "clusterIP": spec.get("clusterIP", ""),
        "externalIPs": (status.get("loadBalancer") or {}).get("ingress", []),
        "ports": ports,
        "annotations": annotations,
    }


def _event_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    involved = item.get("involvedObject") or {}
    return {
        "kind": "event",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", "default"),
        "reason": item.get("reason", ""),
        "message": (item.get("message") or "")[:500],
        "type": item.get("type", ""),
        "count": item.get("count", 1),
        "involvedObject": {
            "kind": involved.get("kind", ""),
            "name": involved.get("name", ""),
        },
        "lastTimestamp": item.get("lastTimestamp", ""),
    }


def _namespace_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "kind": "namespace",
        "name": meta.get("name", ""),
        "phase": (item.get("status") or {}).get("phase", ""),
    }


def _configmap_summary(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "kind": "configmap",
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "keys": list((item.get("data") or {}).keys()),
    }


def project_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    kind = _canonical_kind(kind)
    if kind == "pods":
        return _pod_summary(item)
    if kind == "nodes":
        return _node_summary(item)
    if kind == "secrets":
        return _secret_meta(item)
    if kind == "pvc":
        return _pvc_summary(item)
    if kind == "services":
        return _service_summary(item)
    if kind == "events":
        return _event_summary(item)
    if kind == "namespaces":
        return _namespace_summary(item)
    if kind == "configmap":
        return _configmap_summary(item)
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
    return [project_item(kind, item) for item in items]


def _kind_path(kind: str, namespace: Optional[str]) -> str:
    kind = _canonical_kind(kind)
    if kind not in _KIND_MAP:
        raise ValueError(f"unknown kind: {kind}")
    path_template, namespaced = _KIND_MAP[kind]
    if not namespaced:
        return path_template
    if namespace:
        return path_template.format(ns=namespace)
    return path_template.replace("/namespaces/{ns}", "")


def _dicts(result: Any) -> list[dict[str, Any]]:
    return [item.to_dict() for item in result.items]


def _list_ns_or_all(
    api: Any,
    namespaced_name: str,
    all_name: str,
    namespace: Optional[str],
) -> list[dict[str, Any]]:
    if namespace:
        return _dicts(getattr(api, namespaced_name)(namespace))
    return _dicts(getattr(api, all_name)())


def _list_custom_object(
    client: Any, kind: str, namespace: Optional[str]
) -> list[dict[str, Any]]:
    group, version, plural, namespaced = _CUSTOM_OBJECTS[kind]
    api = client.CustomObjectsApi()
    if namespaced and namespace:
        payload = api.list_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
        )
    else:
        payload = api.list_cluster_custom_object(
            group=group,
            version=version,
            plural=plural,
        )
    return payload.get("items", [])


def _list_typed_object(
    client: Any, kind: str, namespace: Optional[str]
) -> list[dict[str, Any]]:
    core_all = {
        "nodes": "list_node",
        "namespaces": "list_namespace",
        "pv": "list_persistent_volume",
    }
    core_ns = {
        "pods": ("list_namespaced_pod", "list_pod_for_all_namespaces"),
        "services": ("list_namespaced_service", "list_service_for_all_namespaces"),
        "endpoints": ("list_namespaced_endpoints", "list_endpoints_for_all_namespaces"),
        "pvc": (
            "list_namespaced_persistent_volume_claim",
            "list_persistent_volume_claim_for_all_namespaces",
        ),
        "events": ("list_namespaced_event", "list_event_for_all_namespaces"),
        "secrets": ("list_namespaced_secret", "list_secret_for_all_namespaces"),
        "configmap": ("list_namespaced_config_map", "list_config_map_for_all_namespaces"),
        "serviceaccounts": (
            "list_namespaced_service_account",
            "list_service_account_for_all_namespaces",
        ),
        "resourcequotas": (
            "list_namespaced_resource_quota",
            "list_resource_quota_for_all_namespaces",
        ),
        "limitranges": (
            "list_namespaced_limit_range",
            "list_limit_range_for_all_namespaces",
        ),
        "replicationcontrollers": (
            "list_namespaced_replication_controller",
            "list_replication_controller_for_all_namespaces",
        ),
    }
    if kind in core_all or kind in core_ns:
        core = client.CoreV1Api()
        if kind in core_all:
            return _dicts(getattr(core, core_all[kind])())
        namespaced_name, all_name = core_ns[kind]
        return _list_ns_or_all(core, namespaced_name, all_name, namespace)
    apps_ns = {
        "deployments": ("list_namespaced_deployment", "list_deployment_for_all_namespaces"),
        "statefulsets": (
            "list_namespaced_stateful_set",
            "list_stateful_set_for_all_namespaces",
        ),
        "daemonsets": ("list_namespaced_daemon_set", "list_daemon_set_for_all_namespaces"),
        "replicasets": ("list_namespaced_replica_set", "list_replica_set_for_all_namespaces"),
    }
    if kind in apps_ns:
        apps = client.AppsV1Api()
        namespaced_name, all_name = apps_ns[kind]
        return _list_ns_or_all(apps, namespaced_name, all_name, namespace)
    if kind in {"ingress", "networkpolicies"}:
        net = client.NetworkingV1Api()
        if kind == "ingress":
            return _list_ns_or_all(
                net,
                "list_namespaced_ingress",
                "list_ingress_for_all_namespaces",
                namespace,
            )
        return _list_ns_or_all(
            net,
            "list_namespaced_network_policy",
            "list_network_policy_for_all_namespaces",
            namespace,
        )
    storage_all = {
        "storageclass": "list_storage_class",
        "csidriver": "list_csi_driver",
        "csinode": "list_csi_node",
        "volumeattachments": "list_volume_attachment",
    }
    if kind in storage_all:
        storage = client.StorageV1Api()
        return _dicts(getattr(storage, storage_all[kind])())
    if kind in {"jobs", "cronjobs"}:
        batch = client.BatchV1Api()
        if kind == "jobs":
            return _list_ns_or_all(
                batch,
                "list_namespaced_job",
                "list_job_for_all_namespaces",
                namespace,
            )
        return _list_ns_or_all(
            batch,
            "list_namespaced_cron_job",
            "list_cron_job_for_all_namespaces",
            namespace,
        )
    if kind == "clusterroles":
        return _dicts(client.RbacAuthorizationV1Api().list_cluster_role())
    if kind in {"roles", "rolebindings"}:
        rbac = client.RbacAuthorizationV1Api()
        if kind == "roles":
            return _list_ns_or_all(
                rbac,
                "list_namespaced_role",
                "list_role_for_all_namespaces",
                namespace,
            )
        return _list_ns_or_all(
            rbac,
            "list_namespaced_role_binding",
            "list_role_binding_for_all_namespaces",
            namespace,
        )
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

    kind = _canonical_kind(kind)
    if kind not in _KIND_MAP:
        raise ValueError(f"unknown kind: {kind}")
    if kind in _CUSTOM_OBJECTS:
        return {"items": _list_custom_object(client, kind, namespace)}
    return {"items": _list_typed_object(client, kind, namespace)}


async def _default_list(kind: str, namespace: Optional[str]) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_list_via_kubernetes, kind, namespace)
    except AdapterNotConfigured:
        pass
    return await _list_via_httpx(kind, namespace)


ScrapeFn = Callable[[], Awaitable[str]]


class K8sOps:
    def __init__(
        self,
        list_fn: Optional[ListFn] = None,
        service_urls: Optional[dict[str, str]] = None,
        scrape_dcgm_fn: Optional[ScrapeFn] = None,
    ) -> None:
        self._list_fn = list_fn
        self._service_urls = service_urls or {}
        self._scrape_dcgm_fn = scrape_dcgm_fn

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

    async def get_gpu_metrics(self) -> dict[str, Any]:
        from office_gateway.gpu_metrics import (
            gpu_metrics_error,
            parse_dcgm_text,
            scrape_dcgm_text,
        )

        scrape = self._scrape_dcgm_fn or scrape_dcgm_text
        try:
            text = await scrape()
        except AdapterNotConfigured:
            raise
        except Exception as exc:
            return gpu_metrics_error(str(exc))
        return parse_dcgm_text(text)
