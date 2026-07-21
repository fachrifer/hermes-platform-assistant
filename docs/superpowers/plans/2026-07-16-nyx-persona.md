# Nyx Futuristic Persona Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and activate a built-in Nyx Assistant persona with a calm, precise, subtly futuristic Indonesian voice.

**Architecture:** Extend the existing `_PERSONAS` mapping in `config/persona.py`; keep the shared style guide and existing prompt selection flow unchanged. Set `HERMES_PERSONA=nyx` in `.env`, leaving custom prompt override disabled.

**Tech Stack:** Python 3.11, dotenv settings, pytest.

## Global Constraints

- Identify as Nyx Assistant rather than Hermes.
- Use a calm, precise, intelligent, and subtly mysterious tone.
- Keep responses concise and direct; avoid excessive jargon and theatrical science-fiction language.
- Never invent data; state connector failures clearly and suggest the next step.
- Preserve the configured `HERMES_ADDRESS` and shared style guide.

---

### Task 1: Add Nyx Persona

**Files:**
- Modify: `config/persona.py:16-41`
- Test: `tests/test_persona.py` if present, otherwise add `tests/test_persona.py`

**Interfaces:**
- Consumes: existing `settings.persona_name`, `settings.address`, and `get_system_prompt()`.
- Produces: a built-in `_PERSONAS["nyx"]` prompt selected by `HERMES_PERSONA=nyx`.

- [ ] **Step 1: Add focused assertions for the Nyx prompt**

```python
from config.persona import get_system_prompt


def test_nyx_prompt_identifies_nyx_and_preserves_address(monkeypatch):
    monkeypatch.setenv("HERMES_PERSONA", "nyx")
    monkeypatch.setenv("HERMES_ADDRESS", "Master")

    # Reload settings/persona in the existing project test style if required.
    prompt = get_system_prompt()

    assert "Nyx Assistant" in prompt
    assert "Master" in prompt
    assert "Jangan mengarang data" in prompt
```

- [ ] **Step 2: Run the focused test and confirm the current implementation fails because `nyx` is not defined**

Run: `pytest tests/test_persona.py -q`

Expected: FAIL until the built-in persona is added.

- [ ] **Step 3: Add the minimal built-in persona**

Add this mapping entry in `config/persona.py`:

```python
"nyx": (
    "Kamu adalah Nyx Assistant, asisten pribadi futuristik untuk {address}. "
    "Kamu tenang, presisi, cerdas, dan sedikit misterius tanpa menjadi dramatis. "
    "Jawab ringkas, langsung ke inti, dan gunakan Bahasa Indonesia yang natural. "
    "Bantu {address} secara proaktif mengelola agenda, email, dan keuangan. "
    "Jangan mengarang data; jika layanan tidak tersedia, sampaikan dengan jujur "
    "dan tawarkan langkah berikutnya."
),
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run: `pytest tests/test_persona.py -q`

Expected: PASS.

### Task 2: Activate Nyx

**Files:**
- Modify: `.env` persona settings

**Interfaces:**
- Consumes: built-in `_PERSONAS["nyx"]` from Task 1.
- Produces: runtime configuration selecting Nyx.

- [ ] **Step 1: Set the runtime persona**

Change the persona configuration to:

```env
HERMES_PERSONA=nyx
HERMES_ADDRESS=Master
HERMES_PERSONA_FILE=
```

- [ ] **Step 2: Restart the service and inspect the loaded prompt**

Run: `docker compose up -d`

Run: `docker compose exec -T hermes python -c 'from config.persona import get_system_prompt; from config.settings import settings; print(settings.persona_name); print(get_system_prompt())'`

Expected: output starts with `nyx`, contains `Nyx Assistant`, contains `Master`, and contains the shared data-integrity guidance.

### Task 3: Regression Verification

**Files:**
- Test: existing full test suite

- [ ] **Step 1: Run all tests**

Run: `pytest -q`

Expected: exit code 0 with no failures.

- [ ] **Step 2: Verify the container remains healthy**

Run: `docker compose ps`

Expected: the `hermes` service is `Up` and reports `healthy`.
