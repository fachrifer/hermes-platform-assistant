# Hermes Office CML + Qwen3 8B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two CML Applications (`qwen3-8b`, `hermes-office`) plus a Hermes Cloud ingest path so CML stores logs locally and Cloud receives only report + recommendation for Telegram.

**Architecture:** Self-contained code under `cml/`. `hermes-office` polls health/logs, uses local Qwen for log selection + recommendations, stores everything in CML SQLite/files, and POSTs signed report payloads to Hermes Cloud. Cloud endpoint accepts HMAC-authenticated reports without laptop mTLS when CML direct mode is configured. Telegram `/office` serves stored report content.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, httpx, sqlite3, transformers+torch (Qwen serve), HMAC-SHA256

## Global Constraints

- Model: Qwen3 8B only, FP16 on V100 (sm_70); no BF16/FP8 requirement
- Monitor/report only — no infra write actions
- Logs stay in CML; Cloud payload = report + recommendation (+ short selected_logs excerpts)
- Bind CML apps to `CDSW_APP_PORT`
- No laptop relay on CML happy path
- Reuse patterns from `core/office_monitor.py` signing; do not break existing snapshot+relay path

---

### Task 1: Cloud report payload contract + ingest endpoint

**Files:**
- Create: `core/office_report.py`
- Modify: `core/main.py` (add `POST /api/v1/office/reports`)
- Modify: `core/db.py` (save/list office report artifacts if needed)
- Modify: `config/settings.py` (optional `office_cml_direct` / empty mTLS allowed)
- Modify: `core/agent.py` / report rendering path to prefer stored CML reports
- Test: `tests/test_office_report_ingest.py`

**Interfaces:**
- Produces: `validate_office_report(payload) -> dict`, `sign_office_report(payload, secret) -> str`, `OfficeMonitoringService.ingest_report(...)`, DB helpers
- Consumes: existing `settings.office_observer_*`

- [ ] **Step 1: Write failing tests** for validate/sign/ingest and API auth (HMAC ok; bad HMAC 401; when `office_observer_mtls_subject` empty, CML direct allowed without subject header)
- [ ] **Step 2: Implement `core/office_report.py` + DB + endpoint + `/office` read path**
- [ ] **Step 3: pytest pass**
- [ ] **Step 4: Commit** `feat: accept CML office report payloads on Hermes Cloud`

Allowed report root fields: `observer_id`, `generated_at`, `period`, `metrics`, `selected_logs`, `recommendation`, `qwen_assist` (signature is header, not body field — or body field; pick header `X-Hermes-Signature` to match snapshots).

Period values: `status` | `daily` | `weekly` | `monthly`.

---

### Task 2: CML local store (logs + reports queue)

**Files:**
- Create: `cml/hermes_office/store.py`
- Test: `tests/cml/test_office_store.py`

**Interfaces:**
- Produces: `OfficeStore(data_dir)` with `append_logs`, `list_recent_logs`, `purge_logs`, `save_health`, `save_report`, `enqueue_cloud_payload`, `dequeue_cloud_payloads`, `list_reports`

- [ ] **Step 1: Failing tests** for log cap/retention, report save, cloud outbox queue
- [ ] **Step 2: Implement store**
- [ ] **Step 3: pytest pass + commit** `feat: add CML hermes-office local store`

---

### Task 3: Observer + Qwen assist + Reporter

**Files:**
- Create: `cml/hermes_office/observer.py`
- Create: `cml/hermes_office/qwen_assist.py`
- Create: `cml/hermes_office/reporter.py`
- Create: `cml/hermes_office/config.py`
- Test: `tests/cml/test_office_observer_assist_reporter.py`

**Interfaces:**
- Reuse: `office_observer.collectors.HttpServiceCollector` OR thin local copy
- Produces: `run_cycle()` → health + logs → assist → store → enqueue signed cloud payload; `CloudReporter.flush()` POSTs outbox to `HERMES_CLOUD_REPORT_URL`

- [ ] **Step 1: Failing tests** with mocked httpx/Qwen (Qwen down → recommendation `qwen unavailable`; payload has no raw log dump field)
- [ ] **Step 2: Implement modules**
- [ ] **Step 3: pytest pass + commit** `feat: add CML observer, qwen assist, and reporter`

---

### Task 4: Manager UI + CML entrypoint

**Files:**
- Create: `cml/hermes_office/app.py` (FastAPI UI + API)
- Create: `cml/hermes_office/run_app.py` (CDSW_APP_PORT launcher + background loop)
- Create: `cml/hermes_office/requirements.txt`
- Create: `cml/hermes_office/templates/index.html` (simple status/reports/logs)
- Test: `tests/cml/test_office_ui.py`

- [ ] **Step 1: Failing tests** for `/`, `/api/status`, `/api/reports`
- [ ] **Step 2: Implement UI + run_app**
- [ ] **Step 3: pytest pass + commit** `feat: add CML hermes-office Manager UI`

---

### Task 5: Qwen3 8B CML Application

**Files:**
- Create: `cml/qwen3_8b/serve.py` (OpenAI-compatible `/v1/models`, `/v1/chat/completions`, `/health`)
- Create: `cml/qwen3_8b/run_app.py`
- Create: `cml/qwen3_8b/requirements.txt`
- Test: `tests/cml/test_qwen_serve.py` (mock model generate; no GPU required in CI)

- [ ] **Step 1: Failing API shape tests** with injectable fake engine
- [ ] **Step 2: Implement server + lazy real transformers engine behind env `QWEN_MODEL_PATH` / `QWEN_MODEL_ID`
- [ ] **Step 3: pytest pass + commit** `feat: add CML Qwen3 8B OpenAI-compatible app`

---

### Task 6: CML README + env examples

**Files:**
- Create: `cml/README.md`
- Create: `cml/hermes_office/env.example`
- Create: `cml/qwen3_8b/env.example`
- Modify: `docs/superpowers/specs/2026-07-23-hermes-office-cml-design.md` status → Approved

- [ ] **Step 1: Document** two-app CML deploy steps, V100 FP16 notes, env vars, Telegram verification
- [ ] **Step 2: Commit** `docs: add CML deploy guide for Hermes Office and Qwen3`

---

## Execution

User requested **build the apps** → execute **Inline** in this session (executing-plans style, task-by-task with TDD).
