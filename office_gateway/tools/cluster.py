from __future__ import annotations

import re

from office_gateway.k8s_ops import ALLOWED_KINDS
from office_gateway.mig import compare_mig
from office_gateway.tools.core import Param, Tool, ToolError, fit_items

GPU = frozenset({"cluster-gpu"})
_NS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


async def _k8s_get(ctx, args, role):
    namespace = args.get("namespace")
    if namespace and not _NS_RE.fullmatch(namespace):
        raise ToolError("invalid_argument", "namespace is not a valid Kubernetes name")
    result = await ctx.k8s.list_resources(args["kind"], namespace)
    items = result.get("items", [])
    kept, omitted = fit_items(items, budget=1500)
    return {"kind": result.get("kind"), "count": len(items), "items": kept, "omitted": omitted}


async def _mig_map(ctx, args, role):
    mig = await ctx.k8s.get_mig_actual()
    result = compare_mig(ctx.config.mig_expected, mig.get("actual", {}))
    if mig.get("error"):
        result["ok"] = False
        result["error"] = mig["error"]
    return result


async def _gpu_usage(ctx, args, role):
    return await ctx.k8s.get_gpu_metrics()


TOOLS = (
    Tool(
        "k8s_get",
        GPU,
        "List RKE2 resources of one kind (sanitised, bounded). Includes K8s Traefik HTTPRoute/Gateway/IngressRoute.",
        {
            "kind": Param("string", "resource kind", required=True, enum=tuple(sorted(ALLOWED_KINDS))),
            "namespace": Param("string", "namespace; omit for all namespaces", max_length=63),
        },
        _k8s_get,
    ),
    Tool("mig_map", GPU, "MIG profiles found on the GPU nodes compared with the expected layout.", {}, _mig_map),
    Tool("gpu_usage", GPU, "GPU utilisation and memory from DCGM.", {}, _gpu_usage),
)
