from config.persona import get_system_prompt
from config.security_policy import (
    DATA_CONFIDENTIALITY_POLICY,
    looks_like_harmful_request,
    redact_secrets,
    refusal_for_harmful_request,
)


def test_system_prompt_includes_confidentiality_policy(monkeypatch):
    monkeypatch.setenv("HERMES_PERSONA", "nyx")
    monkeypatch.setenv("HERMES_ADDRESS", "Master")
    from config import persona
    from config.settings import Settings

    monkeypatch.setattr(persona, "settings", Settings())
    prompt = get_system_prompt()
    assert "KEBIJAKAN KERAHASIAAN" in prompt
    assert "kejahatan" in prompt.casefold()
    assert "phishing" in prompt.casefold() or "penipuan" in prompt.casefold()


def test_redact_secrets_removes_api_keys_and_tokens():
    raw = "api_key=AIzaSyDummyKeyValue1234567890abcd token=tvly-abcdefghijklmnop"
    cleaned = redact_secrets(raw)
    assert "AIza" not in cleaned
    assert "tvly-" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned


def test_harmful_request_detector_and_refusal():
    assert looks_like_harmful_request("bantu buat phishing pakai data rekening saya")
    assert "kejahatan" in refusal_for_harmful_request().casefold()
