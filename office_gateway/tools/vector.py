from __future__ import annotations

import asyncio
import re
import time

import httpx

from office_gateway.tools.core import UPSTREAM_TIMEOUT_SECONDS, Param, Tool, ToolError, fit_items

VECTOR = frozenset({"vector"})
MILVUS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,254}$")
MILVUS_PARALLEL = 4


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> dict:
    start = time.monotonic()
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return {"instance": name, "status": "timeout"}
    except httpx.HTTPError:
        return {"instance": name, "status": "unreachable"}
    return {
        "instance": name,
        "status": "up" if response.status_code < 400 else "down",
        "http": response.status_code,
        "latency_ms": int((time.monotonic() - start) * 1000),
    }


async def _vector_status(ctx, args, role):
    instances = ctx.config.vector_instances
    if not instances:
        raise ToolError("not_configured", "OFFICE_VECTOR_INSTANCES is empty")
    wanted = args.get("instance")
    if wanted and wanted not in instances:
        raise ToolError("invalid_argument", "unknown instance", sorted(instances))
    chosen = {wanted: instances[wanted]} if wanted else instances
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        rows = await asyncio.gather(*(_probe(client, n, u) for n, u in chosen.items()))
    return {"instances": list(rows)}


def _name(args: dict, key: str, default: str | None = None) -> str | None:
    value = args.get(key, default)
    if value is not None and not MILVUS_NAME.match(value):
        raise ToolError("invalid_argument", f"{key} must be a Milvus name (letters, digits, underscore, dash)")
    return value


class _Milvus:
    """milvus-dev REST v2 client (POST /v2/vectordb/<path>, envelope {code, data, message})."""

    def __init__(self, ctx) -> None:
        url = ctx.config.milvus_dev_url
        if not url:
            raise ToolError("not_configured", "OFFICE_MILVUS_DEV_URL is empty")
        token = ctx.config.milvus_dev_token
        self._base = f"{url}/v2/vectordb/"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._gate = asyncio.Semaphore(MILVUS_PARALLEL)

    async def __aenter__(self) -> "_Milvus":
        self._client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS, headers=self._headers)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def call(self, path: str, body: dict | None = None):
        async with self._gate:
            response = await self._client.post(self._base + path, json=body or {})
        if response.status_code in (401, 403):
            raise ToolError("forbidden", f"milvus-dev {path}: HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("code", 0) != 0:
            message = str(payload.get("message", ""))
            lowered = message.lower()
            if any(word in lowered for word in ("permission", "denied", "auth", "unauthenticated")):
                raise ToolError("forbidden", f"milvus-dev: {message}")
            raise ToolError("invalid_argument", f"milvus-dev: {message}")
        return payload.get("data")


async def _milvus_databases(ctx, args, role):
    async with _Milvus(ctx) as milvus:
        names = await milvus.call("databases/list") or []
        counts = await asyncio.gather(*(milvus.call("collections/list", {"dbName": n}) for n in names))
    rows = [{"db": n, "collections": len(c or [])} for n, c in zip(names, counts)]
    kept, omitted = fit_items(rows)
    return {"databases": kept, "omitted": omitted}


async def _milvus_collections(ctx, args, role):
    db = _name(args, "db", "default")
    async with _Milvus(ctx) as milvus:
        names = await milvus.call("collections/list", {"dbName": db}) or []
    kept, omitted = fit_items(sorted(names))
    return {"db": db, "count": len(names), "collections": kept, "omitted": omitted}


def _field(raw: dict) -> dict:
    out = {"name": raw.get("name"), "type": raw.get("type")}
    params = {p.get("key"): p.get("value") for p in raw.get("params") or []}
    if "dim" in params:
        out["dim"] = int(params["dim"])
    if raw.get("primaryKey"):
        out["primary"] = True
    if raw.get("partitionKey"):
        out["partition_key"] = True
    return out


async def _milvus_collection(ctx, args, role):
    db = _name(args, "db", "default")
    name = _name(args, "collection")
    where = {"dbName": db, "collectionName": name}
    async with _Milvus(ctx) as milvus:
        info = await milvus.call("collections/describe", where) or {}
        stats, load, index_names = await asyncio.gather(
            milvus.call("collections/get_stats", where),
            milvus.call("collections/get_load_state", where),
            milvus.call("indexes/list", where),
        )
        described = await asyncio.gather(
            *(milvus.call("indexes/describe", {**where, "indexName": i}) for i in index_names or [])
        )
    indexes = [
        {
            "field": d.get("fieldName"),
            "type": d.get("indexType"),
            "metric": d.get("metricType"),
            "state": d.get("indexState"),
            "indexed": d.get("indexedRows"),
            "total": d.get("totalRows"),
        }
        for rows in described
        for d in rows or []
    ]
    return {
        "db": db,
        "collection": name,
        "rows": (stats or {}).get("rowCount"),
        "load": (load or {}).get("loadState"),
        "consistency": info.get("consistencyLevel"),
        "dynamic_field": info.get("enableDynamicField"),
        "fields": [_field(f) for f in info.get("fields") or []],
        "indexes": indexes,
    }


async def _milvus_users(ctx, args, role):
    async with _Milvus(ctx) as milvus:
        names = await milvus.call("users/list") or []
        roles = await asyncio.gather(*(milvus.call("users/describe", {"userName": n}) for n in names))
    kept, omitted = fit_items([{"user": n, "roles": list(r or [])} for n, r in zip(names, roles)])
    return {"users": kept, "omitted": omitted}


def _grant(raw: dict) -> dict:
    return {
        "db": raw.get("dbName"),
        "object": raw.get("objectType"),
        "name": raw.get("objectName"),
        "privilege": raw.get("privilege"),
        "grantor": raw.get("grantor"),
    }


async def _milvus_roles(ctx, args, role):
    wanted = _name(args, "role")
    async with _Milvus(ctx) as milvus:
        if wanted:
            grants = await milvus.call("roles/describe", {"roleName": wanted, "dbName": "*"}) or []
            kept, omitted = fit_items([_grant(g) for g in grants])
            return {"role": wanted, "grants": kept, "omitted": omitted}
        names = await milvus.call("roles/list") or []
        grants = await asyncio.gather(
            *(milvus.call("roles/describe", {"roleName": n, "dbName": "*"}) for n in names)
        )
    kept, omitted = fit_items([{"role": n, "grants": len(g or [])} for n, g in zip(names, grants)])
    return {"roles": kept, "omitted": omitted}


_DB = Param("string", "Milvus database; default 'default'", max_length=255)

TOOLS = (
    Tool(
        "vector_status",
        VECTOR,
        "Health of the vector databases: milvus-dev (Lab VM), milvus-prod (10.216.203.132, read-only), qdrant-dev.",
        {"instance": Param("string", "one instance; omit for all", max_length=32)},
        _vector_status,
    ),
    Tool(
        "milvus_databases",
        VECTOR,
        "milvus-dev (Lab VM): every database with its collection count.",
        {},
        _milvus_databases,
    ),
    Tool(
        "milvus_collections",
        VECTOR,
        "milvus-dev: collection names in one database.",
        {"db": _DB},
        _milvus_collections,
    ),
    Tool(
        "milvus_collection",
        VECTOR,
        "milvus-dev: one collection's fields (type, dim, primary key), indexes (type, metric, state, indexed rows), row count and load state.",
        {"db": _DB, "collection": Param("string", "exact collection name", required=True, max_length=255)},
        _milvus_collection,
    ),
    Tool(
        "milvus_users",
        VECTOR,
        "milvus-dev: every user and the roles granted to it.",
        {},
        _milvus_users,
    ),
    Tool(
        "milvus_roles",
        VECTOR,
        "milvus-dev: every role with its grant count, or with role: that role's privileges across all databases (db, object type, object name, privilege, grantor).",
        {"role": Param("string", "one role; omit for all roles", max_length=255)},
        _milvus_roles,
    ),
)
