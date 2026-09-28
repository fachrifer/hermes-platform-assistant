import asyncio
import json

import httpx
import pytest

from gw_helpers import make_config
from office_gateway.reports import queries
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY, tools_for_role
from office_gateway.tools.core import RESULT_CAP, ToolContext, call_tool


class FakeCollector:
    def __init__(self, services):
        self.services = services

    async def collect(self):
        return self.services


class FakeK8s:
    async def list_resources(self, kind, namespace):
        return {"kind": kind, "items": [{"name": f"pod-{i}", "phase": "Running"} for i in range(200)]}

    async def get_mig_actual(self):
        return {"actual": {"1g.18gb": 7}}

    async def get_gpu_metrics(self):
        return {"gpus": [{"index": 0, "util": 40}]}


def _ctx(tmp_path, services=None, **cfg):
    config = make_config(tmp_path, **cfg)
    store = GatewayStore(config.db_path)
    collector = FakeCollector(services or [])
    return ToolContext(config, store, None, None, None, FakeK8s(), collector, None, None)


def _call(ctx, role, name, args=None):
    out = asyncio.run(call_tool(ctx, REGISTRY, role, name, args or {}))
    assert len(json.dumps(out)) <= RESULT_CAP
    return out


def test_role_catalogs():
    assert {t.name for t in tools_for_role("supervisor")} == {"fleet_status"}
    assert {t.name for t in tools_for_role("llm")} == {"llm_status", "list_models"}
    assert {t.name for t in tools_for_role("cluster-gpu")} == {"k8s_get", "mig_map", "gpu_usage"}
    assert {t.name for t in tools_for_role("vector")} == {
        "vector_status", "milvus_databases", "milvus_collections", "milvus_collection", "milvus_users", "milvus_roles",
    }
    assert {t.name for t in tools_for_role("obs")} == {"metrics_query", "grafana_links", "action_status"}
    assert tools_for_role("approver") == []


def test_fleet_status_one_line_per_domain(tmp_path):
    services = [
        {"name": "gateway", "status": "ok"},
        {"name": "hermes-lab-host", "status": "ok"},
        {"name": "aiplatform", "status": "critical"},
        {"name": "hermes-ingress", "status": "unknown"},
    ]
    data = _call(_ctx(tmp_path, services), "supervisor", "fleet_status")["data"]
    lines = data["domains"]
    assert len(lines) == 7
    assert any(line.startswith("lab-host: agent ok") and "aiplatform=critical" in line for line in lines)
    assert any(line.startswith("ingress: agent unknown") for line in lines)
    assert data["pending_approvals"] == 0


def test_k8s_get_is_bounded(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "cluster-gpu", "k8s_get", {"kind": "pods", "namespace": "litellm"})["data"]
    assert data["count"] == 200 and data["omitted"] > 0
    bad = _call(ctx, "cluster-gpu", "k8s_get", {"kind": "secrets-all"})
    assert bad["error"]["category"] == "invalid_argument"


def test_mig_map_and_gpu_usage(tmp_path):
    ctx = _ctx(tmp_path, mig_expected={"1g.18gb": 7})
    assert _call(ctx, "cluster-gpu", "mig_map")["data"]["ok"] is True
    assert _call(ctx, "cluster-gpu", "gpu_usage")["data"]["gpus"][0]["util"] == 40


def test_vector_status_reports_per_instance(tmp_path, monkeypatch):
    def handler(request):
        if "down" in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    ctx = _ctx(tmp_path, vector_instances={"milvus-dev": "http://up/healthz", "milvus-prod": "http://down/healthz"})
    rows = {r["instance"]: r for r in _call(ctx, "vector", "vector_status")["data"]["instances"]}
    assert rows["milvus-dev"]["status"] == "up" and rows["milvus-prod"]["status"] == "unreachable"
    one = _call(ctx, "vector", "vector_status", {"instance": "milvus-dev"})["data"]["instances"]
    assert [r["instance"] for r in one] == ["milvus-dev"]


MILVUS_COLLECTION = {
    "collectionName": "docs",
    "consistencyLevel": "Bounded",
    "enableDynamicField": True,
    "fields": [
        {"name": "id", "type": "VarChar", "primaryKey": True, "params": [{"key": "max_length", "value": "65535"}]},
        {"name": "vector", "type": "FloatVector", "primaryKey": False, "params": [{"key": "dim", "value": "4096"}]},
        {"name": "metadata", "type": "JSON", "primaryKey": False},
    ],
}
MILVUS_GRANTS = {
    "pandita_role": [
        {"dbName": "pandita_ai", "grantor": "timai", "objectName": "*", "objectType": "Collection", "privilege": p}
        for p in ("CreateIndex", "Insert", "Search")
    ],
    "admin": [],
}


def _milvus(monkeypatch, seen=None):
    def handler(request):
        path = request.url.path.removeprefix("/v2/vectordb/")
        body = json.loads(request.content or b"{}")
        if seen is not None:
            seen.append((path, body, request.headers.get("authorization")))
        replies = {
            "databases/list": ["default", "pandita_ai"],
            "users/list": ["root", "timai"],
            "roles/list": list(MILVUS_GRANTS),
            "collections/get_stats": {"rowCount": 8},
            "collections/get_load_state": {"loadProgress": 100, "loadState": "LoadStateLoaded"},
            "indexes/list": ["vector"],
            "indexes/describe": [{
                "fieldName": "vector", "indexName": "vector", "indexType": "HNSW", "metricType": "COSINE",
                "indexState": "Finished", "indexedRows": 8, "totalRows": 8, "pendingRows": 0,
            }],
        }
        if path == "collections/list":
            data = [f"c{i}" for i in range(300)] if body.get("dbName") == "pandita_ai" else []
        elif path == "collections/describe":
            if body.get("collectionName") != "docs":
                return httpx.Response(200, json={"code": 100, "message": "can't find collection[database=default][collection=x]"})
            data = MILVUS_COLLECTION
        elif path == "users/describe":
            data = ["admin"] if body["userName"] == "timai" else []
        elif path == "roles/describe":
            data = MILVUS_GRANTS[body["roleName"]]
        else:
            data = replies[path]
        return httpx.Response(200, json={"code": 0, "data": data})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


def test_milvus_databases_and_collections(tmp_path, monkeypatch):
    _milvus(monkeypatch)
    ctx = _ctx(tmp_path)
    dbs = _call(ctx, "vector", "milvus_databases")["data"]["databases"]
    assert dbs == [{"db": "default", "collections": 0}, {"db": "pandita_ai", "collections": 300}]
    data = _call(ctx, "vector", "milvus_collections", {"db": "pandita_ai"})["data"]
    assert data["count"] == 300 and data["omitted"] > 0 and data["collections"][0] == "c0"


def test_milvus_collection_detail(tmp_path, monkeypatch):
    _milvus(monkeypatch)
    ctx = _ctx(tmp_path)
    data = _call(ctx, "vector", "milvus_collection", {"db": "pandita_ai", "collection": "docs"})["data"]
    assert data["rows"] == 8 and data["load"] == "LoadStateLoaded"
    assert {"name": "vector", "type": "FloatVector", "dim": 4096} in data["fields"]
    assert data["fields"][0]["primary"] is True
    assert data["indexes"] == [{"field": "vector", "type": "HNSW", "metric": "COSINE", "state": "Finished", "indexed": 8, "total": 8}]
    missing = _call(ctx, "vector", "milvus_collection", {"collection": "nope"})
    assert missing["error"]["category"] == "invalid_argument" and "can't find collection" in missing["error"]["detail"]
    bad = _call(ctx, "vector", "milvus_collection", {"collection": "x\"; drop"})
    assert bad["error"]["category"] == "invalid_argument"


def test_milvus_users_and_roles(tmp_path, monkeypatch):
    seen = []
    _milvus(monkeypatch, seen)
    ctx = _ctx(tmp_path, milvus_dev_token="root:secret")
    users = _call(ctx, "vector", "milvus_users")["data"]["users"]
    assert users == [{"user": "root", "roles": []}, {"user": "timai", "roles": ["admin"]}]
    roles = _call(ctx, "vector", "milvus_roles")["data"]["roles"]
    assert roles == [{"role": "pandita_role", "grants": 3}, {"role": "admin", "grants": 0}]
    one = _call(ctx, "vector", "milvus_roles", {"role": "pandita_role"})["data"]
    assert one["role"] == "pandita_role" and len(one["grants"]) == 3
    assert one["grants"][0] == {"db": "pandita_ai", "object": "Collection", "name": "*", "privilege": "CreateIndex", "grantor": "timai"}
    assert all(body.get("dbName") == "*" for path, body, _ in seen if path == "roles/describe")
    assert {header for _, _, header in seen} == {"Bearer root:secret"}


def test_milvus_tools_are_vector_only_and_need_url(tmp_path, monkeypatch):
    _milvus(monkeypatch)
    assert _call(_ctx(tmp_path), "lab-host", "milvus_users")["error"]["category"] == "forbidden"
    off = _call(_ctx(tmp_path, milvus_dev_url=""), "vector", "milvus_users")
    assert off["error"]["category"] == "not_configured"


def test_named_query_render():
    q = queries.render("cpu_busy_pct", instance="10.216.4.80", window="1h")
    assert 'instance=~"^10\\\\.216\\\\.4\\\\.80(:[0-9]+)?$"' in q and "[1h]" in q
    assert queries.render("targets_down") == "up == 0"
    with pytest.raises(ValueError):
        queries.render("cpu_busy_pct", instance='x"}) or vector(1')
    with pytest.raises(ValueError):
        queries.render("nope")


def test_every_named_query_renders_with_and_without_instance():
    for name, query in queries.QUERIES.items():
        rendered = queries.render(name)
        assert not any(p in rendered for p in ("{sel}", "{isel}", "{window}"))
        if query.uses_instance:
            assert "10\\\\.0\\\\.0\\\\.1" in queries.render(name, instance="10.0.0.1")


def test_metrics_query_no_metrics(tmp_path, monkeypatch):
    import office_gateway.tools.obs as obs

    async def empty(url, query):
        return {"status": "success", "data": {"resultType": "vector", "result": []}}

    monkeypatch.setattr(obs, "instant_query", empty)
    ctx = _ctx(tmp_path, metrics_url="http://vm:8428")
    out = _call(ctx, "obs", "metrics_query", {"name": "targets_down"})
    assert out["error"]["category"] == "no_metrics"
    bad = _call(ctx, "obs", "metrics_query", {"name": "up{job=~'.*'}"})
    assert bad["error"]["category"] == "invalid_argument" and "targets_down" in bad["error"]["valid"]


def test_metrics_query_values(tmp_path, monkeypatch):
    import office_gateway.tools.obs as obs

    async def some(url, query):
        return {
            "status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {"instance": "a:9100", "job": "node"}, "value": [0, "12.5"]}]},
        }

    monkeypatch.setattr(obs, "instant_query", some)
    ctx = _ctx(tmp_path, metrics_url="http://vm:8428")
    data = _call(ctx, "obs", "metrics_query", {"name": "cpu_busy_pct"})["data"]
    assert data["series"] == 1 and data["values"][0] == {"labels": {"instance": "a:9100", "job": "node"}, "value": 12.5}
