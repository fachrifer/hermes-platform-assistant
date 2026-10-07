from __future__ import annotations

import asyncio
import base64
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
    """Milvus REST v2 client. Resolves base URL + auth by instance name."""

    def __init__(self, ctx, instance: str = "dev") -> None:
        if instance == "prod":
            url = getattr(ctx.config, "milvus_prod_url", "") or ""
            creds = getattr(ctx.config, "milvus_prod_credentials", "") or ""
        else:
            url = ctx.config.milvus_dev_url
            creds = f"root:{ctx.config.milvus_dev_token}" if ctx.config.milvus_dev_token else ""
        if not url:
            raise ToolError(
                "not_configured",
                f"OFFICE_MILVUS_{instance.upper()}_URL is empty — add it to .env and restart the gateway",
            )
        self._instance = instance
        self._base = f"{url.rstrip('/')}/v2/vectordb/"
        if creds:
            # Milvus REST v2 expects the plain 'user:password' as the bearer token.
            self._headers = {"Authorization": f"Bearer {creds}"}
        else:
            self._headers = {}
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
            raise ToolError("forbidden", f"milvus-{self._instance} {path}: HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("code", 0) != 0:
            message = str(payload.get("message", ""))
            lowered = message.lower()
            if any(word in lowered for word in ("permission", "denied", "auth", "unauthenticated")):
                raise ToolError("forbidden", f"milvus-{self._instance}: {message}")
            raise ToolError("invalid_argument", f"milvus-{self._instance}: {message}")
        return payload.get("data")


_INSTANCE = Param("string", "Milvus instance: 'dev' (default) or 'prod'", max_length=8, enum=("dev", "prod"))
_DB = Param("string", "Milvus database; default 'default'", max_length=255)


async def _milvus_databases(ctx, args, role):
    instance = args.get("instance") or "dev"
    async with _Milvus(ctx, instance) as milvus:
        note = None
        try:
            names = await milvus.call("databases/list") or []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            names = ["default"]
            note = "this Milvus has no databases/list endpoint; only the 'default' database is shown"
        counts = await asyncio.gather(*(milvus.call("collections/list", {"dbName": n}) for n in names))
    rows = [{"db": n, "collections": len(c or [])} for n, c in zip(names, counts)]
    kept, omitted = fit_items(rows)
    out = {"instance": instance, "databases": kept, "omitted": omitted}
    if note:
        out["note"] = note
    return out


async def _milvus_collections(ctx, args, role):
    instance = args.get("instance") or "dev"
    db = _name(args, "db", "default")
    async with _Milvus(ctx, instance) as milvus:
        names = await milvus.call("collections/list", {"dbName": db}) or []
    kept, omitted = fit_items(sorted(names))
    return {"instance": instance, "db": db, "count": len(names), "collections": kept, "omitted": omitted}


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
    instance = args.get("instance") or "dev"
    db = _name(args, "db", "default")
    name = _name(args, "collection")
    where = {"dbName": db, "collectionName": name}
    async with _Milvus(ctx, instance) as milvus:
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
        "instance": instance,
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
    instance = args.get("instance") or "dev"
    async with _Milvus(ctx, instance) as milvus:
        names = await milvus.call("users/list") or []
        roles = await asyncio.gather(*(milvus.call("users/describe", {"userName": n}) for n in names))
    kept, omitted = fit_items([{"user": n, "roles": list(r or [])} for n, r in zip(names, roles)])
    return {"instance": instance, "users": kept, "omitted": omitted}


def _grant(raw: dict) -> dict:
    return {
        "db": raw.get("dbName"),
        "object": raw.get("objectType"),
        "name": raw.get("objectName"),
        "privilege": raw.get("privilege"),
        "grantor": raw.get("grantor"),
    }


async def _milvus_roles(ctx, args, role):
    instance = args.get("instance") or "dev"
    wanted = _name(args, "role")
    async with _Milvus(ctx, instance) as milvus:
        if wanted:
            grants = await milvus.call("roles/describe", {"roleName": wanted, "dbName": "*"}) or []
            kept, omitted = fit_items([_grant(g) for g in grants])
            return {"instance": instance, "role": wanted, "grants": kept, "omitted": omitted}
        names = await milvus.call("roles/list") or []
        grants = await asyncio.gather(
            *(milvus.call("roles/describe", {"roleName": n, "dbName": "*"}) for n in names)
        )
    kept, omitted = fit_items([{"role": n, "grants": len(g or [])} for n, g in zip(names, grants)])
    return {"instance": instance, "roles": kept, "omitted": omitted}


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
        "Every database with its collection count. Works on both dev and prod.",
        {"instance": _INSTANCE},
        _milvus_databases,
    ),
    Tool(
        "milvus_collections",
        VECTOR,
        "Collection names in one database. Works on both dev and prod.",
        {"instance": _INSTANCE, "db": _DB},
        _milvus_collections,
    ),
    Tool(
        "milvus_collection",
        VECTOR,
        "One collection's fields, indexes, row count and load state. Works on both dev and prod.",
        {"instance": _INSTANCE, "db": _DB, "collection": Param("string", "exact collection name", required=True, max_length=255)},
        _milvus_collection,
    ),
    Tool(
        "milvus_users",
        VECTOR,
        "Every user and the roles granted to it. Works on both dev and prod.",
        {"instance": _INSTANCE},
        _milvus_users,
    ),
    Tool(
        "milvus_roles",
        VECTOR,
        "Every role with its grant count, or with role: that role's privileges. Works on both dev and prod.",
        {"instance": _INSTANCE, "role": Param("string", "one role; omit for all roles", max_length=255)},
        _milvus_roles,
    ),
)