from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from office_gateway.config import GatewayConfig
from office_gateway.k8s_ops import (
    ALLOWED_KINDS,
    K8sOps,
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
            "llm-edge": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"grafana": "http://grafana.internal/api/health"},
        write_targets={
            "lab-host": frozenset({"aiplatform-dashboard"}),
            "vector": frozenset({"milvus-standalone", "attu"}),
        },
        vector_env={"milvus-standalone": "dev", "milvus-prod": "prod"},
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
    assert out == [
        {
            "kind": "pods",
            "name": "pod-1",
            "namespace": "default",
            "labels": {"app": "web"},
            "status": {"phase": "Running"},
        }
    ]


def test_sanitize_configmap_names_only():
    items = [{"metadata": {"name": "app-config"}, "data": {"key": "value"}}]
    out = sanitize_items("configmap", items)
    assert out == [{"kind": "configmap", "name": "app-config"}]


def test_list_resources_rejects_unknown_kind():
    ops = K8sOps(list_fn=lambda kind, namespace: {"items": []})

    async def run() -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            await ops.list_resources("secrets", None)

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
    assert result["items"][0] == {"kind": "nodes", "name": "node-1"}


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


def test_gpu_mig_endpoint_supervisor(client):
    response = client.get(
        "/v1/gpu/mig",
        headers={"Authorization": "Bearer tok-sup"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_gpu_mig_forbidden_for_vector(client):
    response = client.get(
        "/v1/gpu/mig",
        headers={"Authorization": "Bearer tok-vec"},
    )
    assert response.status_code == 403


def test_k8s_resources_unknown_kind_returns_400(client):
    response = client.get(
        "/v1/k8s/resources?kind=secrets",
        headers={"Authorization": "Bearer tok-gpu"},
    )
    assert response.status_code == 400


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
    assert body["items"][0] == {"kind": "pods", "name": "pod-1"}


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


def test_allowed_kinds_match_brief():
    assert ALLOWED_KINDS == frozenset(
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
