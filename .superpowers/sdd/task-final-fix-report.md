## 2026-08-20 — Whole-branch Critical/Important fixes

Implemented per-service Hermes secret isolation, Hermes v2026.8 A2A/model
configuration, supervisor dashboard basic auth, kubeconfig-backed Kubernetes
reads, durable gateway self-restart/crash recovery, and bounded/redacted
metrics responses.

### Tests

Command:

```sh
.venv/bin/python -m pytest -v
```

Output:

```text
collected 63 items
tests/test_office_fleet_compose.py ... 13 passed
tests/test_office_gateway_docker.py ... 3 passed
tests/test_office_gateway_metrics.py ... 4 passed
tests/test_office_gateway_mig.py ... 21 passed
tests/test_office_gateway_roles.py ... 22 passed
============================== 63 passed in 1.09s ==============================
```

The compose suite ran `docker compose config` successfully using generated
per-role `.env` files.
