from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from office_gateway.config import GatewayConfig
from office_gateway.k8s_ops import (
    ALLOWED_KINDS,
    K8sOps,
    _list_via_kubernetes,
    count_mig_profiles,
    sanitize_items,
)
from office_gateway.mig import compare_mig

EXPECTED = {
    "1g.18gb": 7,
    "2g.35gb": 2,
    "3g.71gb": 2,
    "4g.71gb": 1,
    "7g.141gb": 1,
}


def _test_config(db_path: str = "/tmp/test-gateway.db") -> GatewayConfig:
    return GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"grafana": "http://grafana.internal/api/health"},
        mig_expected=dict(EXPECTED),
        db_path=db_path,
    )


def test_mig_map_ok_when_counts_match():
    result = compare_mig(EXPECTED, dict(EXPECTED))
    assert result["ok"] is True
    assert result["missing"] == {}
    assert result["extra"] == {}


def test_mig_map_reports_missing_slice():
    actual = dict(EXPECTED)
    actual["1g.18gb"] = 6
    result = compare_mig(EXPECTED, actual)
    assert result["ok"] is False
    assert result["missing"] == {"1g.18gb": 1}


def test_mig_map_reports_extra_slice():
    actual = dict(EXPECTED)
    actual["1g.10gb"] = 1
    result = compare_mig(EXPECTED, actual)
    assert result["ok"] is False
    assert result["extra"] == {"1g.10gb": 1}


def test_count_mig_profiles_from_labels_and_allocatable():
    items = [
        {
            "metadata": {"labels": {"nvidia.com/mig.profile": "1g.18gb"}},
            "status": {"allocatable": {}},
        },
        {
            "metadata": {"labels": {}},
            "status": {"allocatable": {"nvidia.com/mig-2g.35gb": "2"}},
        },
    ]
    assert count_mig_profiles(items) == {"1g.18gb": 1, "2g.35gb": 2}


def test_count_mig_profiles_reads_gpu_product_label():
    items = [
        {
            "metadata": {"labels": {"nvidia.com/gpu.product": "3g.71gb"}},
            "status": {"allocatable": {}},
        }
    ]
    assert count_mig_profiles(items) == {"3g.71gb": 1}


def test_count_mig_profiles_prefers_allocatable_over_label():
    items = [
        {
            "metadata": {"labels": {"nvidia.com/mig.profile": "1g.18gb"}},
            "status": {"allocatable": {"nvidia.com/mig-1g.18gb": "7"}},
        }
    ]
    assert count_mig_profiles(items) == {"1g.18gb": 7}


def test_sanitize_items_projects_allowlisted_fields():
    items = [
        {
            "metadata": {
                "name": "pod-1",
                "namespace": "default",
                "labels": {"app": "web", "secret-token": "leak"},
                "annotations": {"kubectl.kubernetes.io/last-applied": "x"},
            },
            "spec": {"volumes": [{"secret": {"secretName": "s"}}]},
            "status": {"phase": "Running"},
            "data": {"kubeconfig": "leak"},
        }
    ]
    out = sanitize_items("pods", items)
    assert out[0]["name"] == "pod-1"
    assert out[0]["phase"] == "Running"
    assert "leak" not in str(out)
    assert "annotations" not in out[0]


def _gpu_node(snake: bool) -> dict:
    labels = {f"feature.node.kubernetes.io/cpu-cpuid.F{i}": "true" for i in range(120)}
    labels.update({
        "node-role.kubernetes.io/control-plane": "true",
        "node-role.kubernetes.io/worker": "true",
        "nvidia.com/gpu.product": "NVIDIA-H200",
        "nvidia.com/mig.config": "all-balanced",
    })
    info_key, kubelet_key = ("node_info", "kubelet_version") if snake else ("nodeInfo", "kubeletVersion")
    return {
        "metadata": {"name": "gpu-node-1", "labels": labels},
        "spec": {"taints": [{"key": "nvidia.com/gpu", "effect": "NoSchedule"}]},
        "status": {
            "conditions": [
                {"type": "Ready", "status": "True"},
                {"type": "MemoryPressure", "status": "False"},
                {"type": "DiskPressure", "status": "True"},
            ],
            "addresses": [{"type": "InternalIP", "address": "10.216.221.100"}, {"type": "Hostname", "address": "gpu-node-1"}],
            info_key: {kubelet_key: "v1.31.4+rke2r1"},
            "capacity": {"cpu": "192", "memory": "2113544216Ki", "pods": "110"},
            "allocatable": {"cpu": "191", "memory": "2113441816Ki", "nvidia.com/mig-1g.18gb": "7", "nvidia.com/gpu": "0"},
            "images": [{"names": [f"img-{i}"], "sizeBytes": 1} for i in range(200)],
        },
    }


@pytest.mark.parametrize("snake", [False, True])
def test_sanitize_nodes_is_compact(snake):
    out = sanitize_items("nodes", [_gpu_node(snake)])[0]
    assert out == {
        "kind": "nodes",
        "name": "gpu-node-1",
        "roles": ["control-plane", "worker"],
        "ready": "True",
        "pressure": ["DiskPressure"],
        "unschedulable": False,
        "internalIP": "10.216.221.100",
        "kubelet": "v1.31.4+rke2r1",
        "cpu": "192",
        "memory": "2113544216Ki",
        "gpu": {"nvidia.com/mig-1g.18gb": "7", "nvidia.com/gpu": "0"},
        "gpuProduct": "NVIDIA-H200",
        "migConfig": "all-balanced",
        "taints": ["nvidia.com/gpu:NoSchedule"],
    }
    assert len(str(out)) < 1000


def test_sanitize_pods_reads_kubernetes_client_snake_case():
    item = {
        "metadata": {"name": "p", "namespace": "gpu-operator"},
        "spec": {"node_name": "gpu-node-1", "containers": [{"name": "c", "image": "i"}]},
        "status": {"phase": "Running", "pod_ip": "10.42.0.7", "container_statuses": [{"restart_count": 3}]},
    }
    out = sanitize_items("pods", [item])[0]
    assert (out["node"], out["podIP"], out["restartCount"]) == ("gpu-node-1", "10.42.0.7", 3)


def test_sanitize_configmap_keys_without_values():
    items = [{"metadata": {"name": "app-config"}, "data": {"key": "value"}}]
    out = sanitize_items("configmap", items)
    assert out == [{"kind": "configmap", "name": "app-config", "namespace": "", "keys": ["key"]}]


def test_sanitize_secrets_metadata_only():
    items = [{"metadata": {"name": "db"}, "type": "Opaque", "data": {"password": "cGFzcw=="}}]
    out = sanitize_items("secrets", items)
    assert out[0]["name"] == "db"
    assert "cGFzcw==" not in str(out)
    assert "data" not in out[0]


def test_list_resources_rejects_unknown_kind():
    ops = K8sOps(list_fn=lambda kind, namespace: {"items": []})

    async def run() -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            await ops.list_resources("bogus", None)

    asyncio.run(run())


def test_list_resources_uses_injected_list_fn():
    seen: list[tuple[str, str | None]] = []

    async def list_fn(kind: str, namespace: str | None) -> dict:
        seen.append((kind, namespace))
        return {"items": [{"metadata": {"name": "node-1"}}]}

    ops = K8sOps(list_fn=list_fn)

    async def run() -> dict:
        return await ops.list_resources("nodes", None)

    result = asyncio.run(run())
    assert seen == [("nodes", None)]
    assert result["kind"] == "nodes"
    assert (result["items"][0]["kind"], result["items"][0]["name"]) == ("nodes", "node-1")


def test_get_mig_actual_adapter_not_configured():
    ops = K8sOps()

    async def run() -> dict:
        return await ops.get_mig_actual()

    result = asyncio.run(run())
    assert result["actual"] == {}
    assert result["error"] == "adapter not configured"
    assert "OFFICE_K8S" not in str(result)
    assert "http" not in str(result).lower()


def test_get_mig_actual_counts_profiles():
    async def list_fn(kind: str, namespace: str | None) -> dict:
        assert kind == "nodes"
        return {
            "items": [
                {
                    "metadata": {"labels": {"nvidia.com/mig.profile": "1g.18gb"}},
                    "status": {"allocatable": {"nvidia.com/mig-1g.18gb": "7"}},
                }
            ]
        }

    ops = K8sOps(list_fn=list_fn)

    async def run() -> dict:
        return await ops.get_mig_actual()

    result = asyncio.run(run())
    assert result["actual"] == {"1g.18gb": 7}
    assert "error" not in result


@pytest.fixture
def client(tmp_path):
    from office_gateway.app import create_app

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))

    async def list_fn(kind: str, namespace: str | None) -> dict:
        if kind == "nodes":
            return {
                "items": [
                    {
                        "metadata": {"labels": {"nvidia.com/mig.profile": profile}},
                        "status": {"allocatable": {}},
                    }
                    for profile, count in EXPECTED.items()
                    for _ in range(count)
                ]
            }
        return {"items": []}

    k8s = K8sOps(list_fn=list_fn)
    return TestClient(create_app(config, k8s_ops=k8s))


def test_gpu_mig_endpoint_cluster_gpu(client):
    response = client.get(
        "/v1/gpu/mig",
        headers={"Authorization": "Bearer tok-gpu"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["expected"] == EXPECTED
    assert body["actual"] == EXPECTED


def test_gpu_mig_forbidden_for_supervisor(client):
    response = client.get(
        "/v1/gpu/mig",
        headers={"Authorization": "Bearer tok-sup"},
    )
    assert response.status_code == 403


def test_gpu_mig_forbidden_for_vector(client):
    response = client.get(
        "/v1/gpu/mig",
        headers={"Authorization": "Bearer tok-vec"},
    )
    assert response.status_code == 403


def test_k8s_resources_unknown_kind_returns_400(client):
    response = client.get(
        "/v1/k8s/resources?kind=bogus",
        headers={"Authorization": "Bearer tok-gpu"},
    )
    assert response.status_code == 400


def test_cluster_gpu_k8s_allows_gateway_and_httproute(client):
    for kind in ("gateway", "httproute"):
        response = client.get(
            f"/v1/k8s/resources?kind={kind}",
            headers={"Authorization": "Bearer tok-gpu"},
        )
        assert response.status_code == 200


def test_llm_k8s_forbidden(client):
    response = client.get(
        "/v1/k8s/resources?kind=pods&namespace=default",
        headers={"Authorization": "Bearer tok-llm"},
    )
    assert response.status_code == 403


def test_cluster_gpu_k8s_keeps_full_allowed_kinds(client):
    response = client.get(
        "/v1/k8s/resources?kind=pods&namespace=default",
        headers={"Authorization": "Bearer tok-gpu"},
    )
    assert response.status_code == 200


def test_k8s_resources_strips_secrets(client):
    from office_gateway.app import create_app

    config = replace(_test_config(), db_path="/tmp/test-gateway.db")

    async def list_fn(kind: str, namespace: str | None) -> dict:
        return {
            "items": [
                {
                    "metadata": {"name": "pod-1"},
                    "spec": {"token": "secret-token"},
                }
            ]
        }

    k8s = K8sOps(list_fn=list_fn)
    api = TestClient(create_app(config, k8s_ops=k8s))
    response = api.get(
        "/v1/k8s/resources?kind=pods&namespace=default",
        headers={"Authorization": "Bearer tok-gpu"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["name"] == "pod-1"
    assert "secret-token" not in response.text


def test_list_resources_gateway_honors_namespace():
    seen: list[tuple[str, str | None]] = []

    async def list_fn(kind: str, namespace: str | None) -> dict:
        seen.append((kind, namespace))
        return {"items": [{"metadata": {"name": "gw-1", "namespace": namespace}}]}

    ops = K8sOps(list_fn=list_fn)

    async def run() -> dict:
        return await ops.list_resources("gateway", "edge")

    result = asyncio.run(run())
    assert seen == [("gateway", "edge")]
    assert result["items"][0]["name"] == "gw-1"
    assert result["items"][0]["namespace"] == "edge"


def test_allowed_kinds_cover_cluster_gpu_reads():
    assert {
        "nodes",
        "pods",
        "deployments",
        "gateway",
        "httproute",
        "ingressroute",
        "storageclass",
        "migpolicy",
        "configmap",
    } <= ALLOWED_KINDS


def test_kubernetes_client_prefers_kubeconfig_then_office_kubeconfig(monkeypatch):
    loaded: list[str | None] = []

    class ConfigException(Exception):
        pass

    fake_config = SimpleNamespace(
        ConfigException=ConfigException,
        load_incluster_config=lambda: (_ for _ in ()).throw(ConfigException()),
        load_kube_config=lambda config_file=None: loaded.append(config_file),
    )
    fake_client = SimpleNamespace(
        CoreV1Api=lambda: SimpleNamespace(
            list_node=lambda: SimpleNamespace(items=[])
        )
    )
    monkeypatch.setitem(
        sys.modules,
        "kubernetes",
        SimpleNamespace(client=fake_client, config=fake_config),
    )
    monkeypatch.setenv("KUBECONFIG", "/mounted/kubeconfig")
    monkeypatch.setenv("OFFICE_KUBECONFIG", "/operator/kubeconfig")

    _list_via_kubernetes("nodes", None)
    assert loaded == ["/mounted/kubeconfig"]

    loaded.clear()
    monkeypatch.delenv("KUBECONFIG")
    _list_via_kubernetes("nodes", None)
    assert loaded == ["/operator/kubeconfig"]


def test_gateway_requirements_include_kubernetes_client():
    requirements = Path("office_gateway/requirements.txt").read_text().splitlines()
    assert any(line.split("=", 1)[0] == "kubernetes" for line in requirements)
