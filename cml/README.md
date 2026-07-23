# Hermes Office + Qwen3 8B on Cloudera Machine Learning

Deploy two **Applications** in the same CML project.

| Application | Script | Runtime | Role |
|---|---|---|---|
| `qwen3-8b` | `cml/qwen3_8b/run_app.py` | GPU (V100 32GB) | OpenAI-compatible Qwen3 8B |
| `hermes-office` | `cml/hermes_office/run_app.py` | CPU | Monitor, local logs, Manager UI, report to Hermes Cloud |

Design: `docs/superpowers/specs/2026-07-23-hermes-office-cml-design.md`

## Prerequisites

- CML project with network egress to Hermes Cloud HTTPS endpoint
- NVIDIA V100 32GB (or compatible) for Qwen
- Model weights available on project filesystem or downloadable as `Qwen/Qwen3-8B`
- Hermes Cloud configured for **CML direct** mode:
  - `OFFICE_OBSERVER_ID=cml-office-1` (example)
  - `OFFICE_OBSERVER_SHARED_SECRET=<long-secret>`
  - `OFFICE_OBSERVER_MTLS_SUBJECT=` **empty** (required for `/api/v1/office/reports`)

## 1. Deploy Qwen3 8B

1. Install deps from `cml/qwen3_8b/requirements.txt` in the CML session/runtime.
2. Set env (see `cml/qwen3_8b/env.example`):
   - `QWEN_MODEL_ID=Qwen/Qwen3-8B` or `QWEN_MODEL_PATH=/path/to/weights`
3. Create Application:
   - Script: `cml/qwen3_8b/run_app.py`
   - Request GPU with ≥32GB VRAM
4. After start, open the app URL and verify:
   - `GET /health` → `{"status":"ok"}`
   - `GET /v1/models`
   - `POST /v1/chat/completions`

**V100 notes:** engine loads with `torch.float16` (not BF16/FP8). Prefer a CUDA build that still supports `sm_70`.

Copy the public/internal Application URL for `QWEN_BASE_URL`.

## 2. Deploy Hermes Office

1. Install deps from `cml/hermes_office/requirements.txt` (repo root must be on `PYTHONPATH`).
2. Set env from `cml/hermes_office/env.example`:
   - `OFFICE_OBSERVER_ID` / `OFFICE_OBSERVER_SHARED_SECRET` (must match Cloud)
   - `HERMES_CLOUD_REPORT_URL=https://<hermes-cloud>/api/v1/office/reports`
   - `QWEN_BASE_URL=<qwen application url>`
   - `OFFICE_SERVICE_URLS=qwen3-8b=<qwen-url>/health`
   - `OFFICE_LOG_SOURCES=qwen3-8b=/path/to/optional.log` (optional)
   - `OFFICE_DATA_DIR` under project persistent storage
3. Create Application:
   - Script: `cml/hermes_office/run_app.py`
   - CPU is enough
4. Open the app URL → Manager UI shows health, reports, local logs.

## 3. Verify end-to-end

1. Wait one cycle (~5 minutes, or lower `OFFICE_INTERVAL_SECONDS` for testing).
2. CML UI shows a status report + recommendation (or `qwen unavailable` if Qwen is down).
3. On Telegram (Hermes Cloud): `/office status` or `/office daily` shows the same recommendation text.
4. Confirm Hermes Cloud storage has **no raw CML logs** — only report content.

## Local dry-run (without CML)

```bash
# Terminal A — fake Qwen not required for unit tests
pytest tests/cml_apps tests/test_office_report_ingest.py -q

# Optional: run office UI against empty store
OFFICE_OBSERVER_ID=cml-office-1 \
OFFICE_OBSERVER_SHARED_SECRET=dev-secret \
HERMES_CLOUD_REPORT_URL=http://127.0.0.1:9/api/v1/office/reports \
QWEN_BASE_URL=http://127.0.0.1:9 \
OFFICE_SERVICE_URLS=qwen3-8b=http://127.0.0.1:9/health \
OFFICE_DATA_DIR=./data/hermes_office \
CDSW_APP_PORT=8088 \
python -m cml.hermes_office.run_app
```

## Security

- Monitor/report only — no restart/scale/secret access
- Logs remain in `OFFICE_DATA_DIR`
- Cloud receives signed report + recommendation (+ short selected log excerpts only)
