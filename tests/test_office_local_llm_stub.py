from pathlib import Path

LOCAL = Path("deploy/office-assistant/docker-compose.local.yml")


def test_local_compose_keeps_models_env_llm():
    text = LOCAL.read_text()
    assert "office-llm-stub" not in text
    assert "OPENAI_BASE_URL:" not in text
    assert "127.0.0.1:9119:9119" in text
    assert "127.0.0.1:9120:80" in text
