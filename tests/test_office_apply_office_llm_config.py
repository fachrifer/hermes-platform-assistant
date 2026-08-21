import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path("deploy/office-assistant/scripts/apply-office-llm-config.py")
CONT_INIT = Path("deploy/office-assistant/scripts/hermes-office-llm-cont-init.sh")


def _load():
    spec = importlib.util.spec_from_file_location("apply_office_llm_config", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cont_init_uses_with_contenv_and_python_stamp():
    assert CONT_INIT.is_file()
    text = CONT_INIT.read_text()
    assert text.splitlines()[0] == "#!/command/with-contenv sh"
    assert "apply-office-llm-config.py" in text


def test_expand_template_writes_literal_key_not_placeholder():
    mod = _load()
    stamped = mod.expand_template(
        "api_key: ${OPENAI_API_KEY}\nbase_url: ${OPENAI_BASE_URL}\n"
        "default: ${OPENAI_MODEL}\n",
        {
            "OPENAI_API_KEY": "sk-office-test-key",
            "OPENAI_BASE_URL": "http://10.216.221.100/llm/v1",
            "OPENAI_MODEL": "qwen3.8-fast",
        },
    )
    assert "sk-office-test-key" in stamped
    assert "${OPENAI_API_KEY}" not in stamped
    assert "${OPENAI_BASE_URL}" not in stamped
    assert "${OPENAI_MODEL}" not in stamped


def test_expand_template_rejects_empty_key():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.expand_template(
            "api_key: ${OPENAI_API_KEY}\n",
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "http://example/v1",
                "OPENAI_MODEL": "m",
            },
        )


def test_main_stamps_config_and_overrides_stale_home_env(tmp_path, monkeypatch):
    mod = _load()
    template = tmp_path / "template.yaml"
    template.write_text("api_key: ${OPENAI_API_KEY}\nbase_url: ${OPENAI_BASE_URL}\n")
    home = tmp_path / "data"
    home.mkdir()
    (home / ".env").write_text("OPENAI_API_KEY=stale-volume-key\nOTHER=keep\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OFFICE_HERMES_CONFIG_TEMPLATE", str(template))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-compose-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://litellm/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen3.8-fast")

    mod.main()

    stamped = (home / "config.yaml").read_text()
    assert "sk-compose-key" in stamped
    assert "${OPENAI_API_KEY}" not in stamped
    assert "stale-volume-key" not in stamped
    env = (home / ".env").read_text()
    assert "OPENAI_API_KEY=sk-compose-key" in env
    assert "stale-volume-key" not in env
    assert "OTHER=keep" in env
    assert "OPENAI_BASE_URL=http://litellm/v1" in env
    assert "OPENAI_MODEL=qwen3.8-fast" in env


def test_expand_template_keeps_fast_start_tuning():
    mod = _load()
    template = Path("deploy/office-assistant/hermes/lab-host/config.yaml").read_text()
    stamped = mod.expand_template(
        template,
        {
            "OPENAI_API_KEY": "sk-office-test-key",
            "OPENAI_BASE_URL": "http://10.216.221.100/llm/v1",
            "OPENAI_MODEL": "qwen3.8-fast",
        },
    )
    assert 'backend: "off"' in stamped
    assert "mcp_servers: {}" in stamped
    assert "request_timeout_seconds: 60" in stamped
    assert "platform_toolsets:" in stamped
    assert "    - hermes-cli" not in stamped
    assert "${OPENAI_API_KEY}" not in stamped
    assert "sk-office-test-key" in stamped
