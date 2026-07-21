# Tavily Search and Gemini Quota Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tavily-backed web/news search and a `/quota` command for Tavily usage plus Gemini connectivity and official quota metadata when configured.

**Architecture:** Add a stateless `TavilyConnector` for search and usage HTTP calls. Extend the existing intent router and `HermesAgent` with a `web_search` action, then expose `/quota` through a quota formatter that combines Tavily usage with a separate Gemini quota reader using service-account credentials.

**Tech Stack:** Python 3.11, `requests`, `google-auth`, Google API discovery client, Telegram commands, pytest.

## Global Constraints

- Use `TAVILY_API_KEY` and never log its value.
- Tavily search must support general web and news queries and return source URLs.
- `/quota` must report exact Tavily usage from `/usage` when available.
- Gemini quota must never be estimated; unavailable metrics must be reported explicitly.
- Gmail/Calendar OAuth credentials remain separate from Gemini quota credentials.
- Missing keys, quota exhaustion, and upstream failures must not prevent Hermes from starting.

---

### Task 1: Configuration and Tavily Connector

**Files:**
- Modify: `config/settings.py` Google/Gemini settings section
- Modify: `.env.example` after `GEMINI_MODEL`
- Create: `connectors/tavily.py`
- Test: `tests/test_tavily.py`

**Interfaces:**
- Produces `TavilyConnector.search(query: str, news: bool = False, max_results: int = 5) -> list[dict]`.
- Produces `TavilyConnector.usage() -> dict`.
- Each result contains `title`, `url`, and `snippet`.

- [ ] **Step 1: Write failing connector tests**

```python
from connectors.tavily import TavilyConnector


def test_search_normalizes_tavily_results(monkeypatch):
    connector = TavilyConnector(api_key="test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "A", "url": "https://a", "content": "Snippet"}]}

    monkeypatch.setattr("connectors.tavily.requests.post", lambda *args, **kwargs: Response())

    assert connector.search("berita terbaru", news=True) == [
        {"title": "A", "url": "https://a", "snippet": "Snippet"}
    ]


def test_search_without_key_returns_empty():
    assert TavilyConnector(api_key="").search("test") == []


def test_usage_returns_tavily_payload(monkeypatch):
    connector = TavilyConnector(api_key="test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"key": {"usage": 12, "limit": 1000}}

    monkeypatch.setattr("connectors.tavily.requests.get", lambda *args, **kwargs: Response())

    assert connector.usage()["key"]["usage"] == 12
```

- [ ] **Step 2: Run tests and confirm they fail because the connector is absent**

Run: `PYTHONPATH=. pytest tests/test_tavily.py -q`

Expected: collection failure with `ModuleNotFoundError: connectors.tavily`.

- [ ] **Step 3: Add settings and connector**

Add these settings fields:

```python
tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY"))
gemini_quota_project_id: str = field(default_factory=lambda: _get("GEMINI_QUOTA_PROJECT_ID"))
gemini_quota_credentials: str = field(
    default_factory=lambda: _get("GEMINI_QUOTA_CREDENTIALS")
)
```

Implement the connector with `requests.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {api_key}"}, json={"query": query, "topic": "news" if news else "general", "max_results": max_results})` and `requests.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {api_key}"})`. Catch `requests.RequestException` and return `[]` or `{}` while logging only the exception type/message.

- [ ] **Step 4: Add environment documentation**

Add to `.env.example`:

```env
# --- Web search ---
TAVILY_API_KEY=

# --- Gemini official quota (optional service account) ---
GEMINI_QUOTA_PROJECT_ID=
GEMINI_QUOTA_CREDENTIALS=/app/credentials/gemini-quota-service-account.json
```

- [ ] **Step 5: Run focused tests and confirm they pass**

Run: `PYTHONPATH=. pytest tests/test_tavily.py -q`

Expected: all Tavily tests pass.

### Task 2: Search Routing and Response Formatting

**Files:**
- Modify: `core/agent.py` tool descriptions, constructor, and message handling
- Modify: `core/llm_client.py` intent documentation if needed
- Test: `tests/test_search_routing.py`

**Interfaces:**
- `HermesAgent` owns `self.tavily`.
- `handle_message()` accepts `{"action": "web_search", "args": {"query": str, "news": bool}}`.

- [ ] **Step 1: Write the failing routing test**

```python
import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_web_search_returns_cited_sources(monkeypatch):
    agent = HermesAgent.__new__(HermesAgent)
    agent.tavily = type("Search", (), {"search": lambda self, query, news=False: [
        {"title": "Berita", "url": "https://example.test", "snippet": "Isi"}
    ]})()
    async def summarize(self, prompt):
        return "Ringkasan"

    agent.llm = type("LLM", (), {"summarize": summarize})()
    agent.memory = type("Memory", (), {"add": lambda *args: None, "recent": lambda *args: []})()

    result = await agent.handle_message(
        "berita terbaru",
        "berita terbaru",
        plan={"action": "web_search", "args": {"query": "berita terbaru", "news": True}},
    )

    assert result == "Ringkasan"
```

- [ ] **Step 2: Run the test and confirm it fails because `web_search` is not routed**

Run: `PYTHONPATH=. pytest tests/test_search_routing.py -q`

Expected: FAIL because `HermesAgent` has no Tavily search branch.

- [ ] **Step 3: Add `web_search` to `_TOOLS_DESC` and route it**

Add an intent description:

```python
'- web_search: cari informasi web atau berita terbaru. args: {"query": string, "news": boolean}\n'
```

In `__init__`, instantiate `self.tavily = TavilyConnector()` and include a helper that formats result sources into the summarization prompt. If no results exist, return a clear unavailable message without calling Gemini.

- [ ] **Step 4: Run focused routing tests**

Run: `PYTHONPATH=. pytest tests/test_search_routing.py -q`

Expected: PASS.

### Task 3: Gemini Quota Reader and `/quota`

**Files:**
- Create: `core/quota.py`
- Modify: `core/agent.py` to expose quota data
- Modify: `core/telegram_bot.py` command registry, help, and handler
- Test: `tests/test_quota.py`

**Interfaces:**
- `QuotaService.tavily_usage() -> dict`.
- `QuotaService.gemini_status() -> dict`.
- `QuotaService.render() -> str`.
- `HermesAgent.quota_report() -> str`.

- [ ] **Step 1: Write failing quota tests**

```python
from core.quota import format_tavily_usage


def test_format_tavily_usage_includes_remaining_credits():
    text = format_tavily_usage({"key": {"usage": 125, "limit": 1000}})
    assert "125 / 1.000" in text
    assert "875" in text


def test_format_tavily_usage_handles_unavailable_data():
    assert "belum tersedia" in format_tavily_usage({})
```

- [ ] **Step 2: Run the tests and confirm they fail because quota helpers are absent**

Run: `PYTHONPATH=. pytest tests/test_quota.py -q`

Expected: collection failure because `core.quota` does not exist.

- [ ] **Step 3: Implement quota service**

Use Tavily's `usage()` payload for exact current-cycle credits. For Gemini, first report whether `GEMINI_API_KEY` is configured and use a lightweight model call only when explicitly requested by `/quota`; if service-account configuration exists, load credentials with `google.oauth2.service_account.Credentials.from_service_account_file` and query Google Cloud service usage/monitoring. Return `available=False` with a clear explanation for missing credentials, missing project ID, permission errors, or unavailable metrics. Never print credential contents.

- [ ] **Step 4: Add `/quota` to Telegram**

Register `CommandHandler("quota", self.cmd_quota)`, implement `cmd_quota()` using `self.agent.quota_report()`, and add `/quota — cek quota Tavily dan Gemini` to `/help`.

- [ ] **Step 5: Run quota-focused tests**

Run: `PYTHONPATH=. pytest tests/test_quota.py -q`

Expected: PASS.

### Task 4: Configuration and Integration Verification

**Files:**
- Modify: `.env.example` if any configuration text is missing
- Test: full suite

- [ ] **Step 1: Configure the Tavily key privately in `.env` without exposing it in logs**

Set only:

```env
TAVILY_API_KEY=your-private-tavily-key
```

Replace the value locally with the key from Tavily. Do not paste the key into
source files, tests, logs, or chat messages.

Do not add the Gemini service-account JSON until it has been placed in the OneDrive `credentials/` folder.

- [ ] **Step 2: Run the full test suite**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Rebuild and verify the service**

Run: `docker compose up --build -d`

Run: `docker compose exec -T hermes python -c 'from core.quota import QuotaService; print(QuotaService().render())'`

Expected: a quota report is printed without exposing API keys, and unavailable Gemini credentials are reported explicitly.

- [ ] **Step 4: Verify container health**

Run: `docker compose ps`

Expected: `hermes` reports `healthy`.
