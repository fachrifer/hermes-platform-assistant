# Task Final Fix Report — Important-only pass

**Date:** 2026-08-20  
**Branch:** feat/office-multi-agent-fleet

## Scope

Final Important-only fixes without reopening plan-mandated supervisor read routes for `/v1/status`, `/v1/grafana/links`, `/v1/metrics/query`.

## Changes

1. **metrics.py** — Expanded banned label/query substrings (`credential`, `passwd`, `bearer`, `private_key`). Capped vector/matrix to 50 series and 50 samples per series. Tests added/updated.
2. **llm-edge k8s** — Handler returns 403 when `role==llm-edge` and `kind` not in `{httproute, gateway}`. `cluster-gpu` retains full `ALLOWED_KINDS`.
3. **k8s_ops** — Sync kubernetes client runs via `asyncio.to_thread` in `_default_list`.
4. **kubeconfig** — `office-gateway-init` copies mounted kubeconfig to volume path with `chown 10001:10001` / `chmod 0640`; gateway `KUBECONFIG` points there. README updated. Skips copy when source absent.
5. **collectors** — `collect()` uses `asyncio.gather` for parallel service probes.

## Verification

```
67 passed in 1.17s
```

## Files touched

- `office_gateway/metrics.py`, `app.py`, `k8s_ops.py`, `collectors.py`
- `deploy/office-assistant/docker-compose.yml`, `README.md`
- `tests/test_office_gateway_metrics.py`, `test_office_gateway_mig.py`, `test_office_fleet_compose.py`
