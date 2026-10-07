from pathlib import Path

import pytest
import yaml

HERMES = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant" / "hermes"
ROLES = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
SPECIALISTS = ROLES[1:]
SURFACES = ("cli", "tui", "api_server", "gui", "desktop", "dashboard", "web")
MODELS = ("qwen3.8-fast", "qwen3.8-reasoning", "qwen3.8-reasoning-xhigh")
MUST_DISABLE = {"terminal", "file", "web", "browser", "code_execution", "delegation", "memory",
                "session_search", "todo", "kanban", "clarify", "a2a"}


def _cfg(role):
    return yaml.safe_load((HERMES / role / "config.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("role", ROLES)
def test_brakes_and_thinking(role):
    cfg = _cfg(role)
    agent = cfg["agent"]
    assert agent["name"] == role
    assert agent["max_turns"] == 6
    assert agent["api_max_retries"] == 1
    assert agent["reasoning_effort"] == "none"
    assert "offline" not in agent
    assert MUST_DISABLE <= set(agent["disabled_toolsets"])
    provider = cfg["providers"]["office-litellm"]
    assert provider["key_env"] == "OFFICE_LLM_API_KEY"
    assert provider["api"] == "${OFFICE_LLM_BASE_URL}"
    assert provider["request_timeout_seconds"] == 60
    assert provider["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    # The model is switched at runtime (/model) and the choice is saved back to config.yaml.
    assert cfg["model"]["default"] in MODELS
    assert cfg["model"]["provider"] == "office-litellm"
    assert set(cfg["model"]) <= {"default", "provider", "thinking_level"}
    guard = cfg["tool_loop_guardrails"]
    assert guard["hard_stop_enabled"] is True
    assert set(guard["hard_stop_after"].values()) == {2}


@pytest.mark.parametrize("role", ROLES)
def test_mcp_only_tools_on_every_surface(role):
    cfg = _cfg(role)
    office = cfg["mcp_servers"]["office"]
    assert office["url"] == "http://office-gateway:8080/mcp"
    assert office["headers"] == {"Authorization": "Bearer ${OFFICE_GATEWAY_TOKEN}"}
    assert (office["timeout"], office["connect_timeout"]) == (15, 5)
    assert set(cfg["mcp_servers"]) == {"office"}
    assert set(cfg["platform_toolsets"]) == set(SURFACES)
    for surface in SURFACES:
        assert cfg["platform_toolsets"][surface] == ["mcp-office", "skills"]
    assert cfg["skills"]["auto_load"] == [f"office-{role}"]
    assert cfg["mcp"]["discovery_concurrency"] == 1
    assert cfg["tools"]["tool_search"]["enabled"] == "off"
    assert cfg["tools"]["connectors"]["enabled"] is False


@pytest.mark.parametrize("role", ROLES)
def test_no_self_improvement(role):
    cfg = _cfg(role)
    assert cfg["_config_version"] == 45
    skills = cfg["skills"]
    assert skills["creation_nudge_interval"] == 0
    assert skills["write_approval"] is True
    assert skills["project_discovery"] is False
    assert cfg["curator"]["enabled"] is False
    assert cfg["auxiliary"]["background_review"]["enabled"] is False
    assert cfg["memory"] == {"memory_enabled": False, "user_profile_enabled": False}


@pytest.mark.parametrize("role", ROLES)
def test_airgap_settings(role):
    cfg = _cfg(role)
    assert cfg["model_catalog"] == {"enabled": False}
    for key in ("office-litellm", "custom:office-litellm"):
        override = cfg["model_overrides"][key]["qwen3.8-fast"]
        assert override["supports_reasoning"] is False and override["supports_tools"] is True
        assert isinstance(override["context_window"], int) and override["context_window"] >= 32768
    assert cfg["auxiliary"]["title_generation"]["model_upgrade_enabled"] is False
    assert cfg["network"]["force_ipv4"] is True
    assert cfg["updates"]["check"] is False
    assert cfg["browser"]["backend"] == "off"
    assert cfg["security"]["allow_lazy_installs"] is False
    assert cfg["gateway"]["platforms"]["api_server"]["enabled"] is True
    assert "a2a" not in cfg["gateway"]["platforms"]
    assert "cron" not in cfg and "kanban" not in cfg and "a2a_agents" not in cfg


def test_supervisor_peers():
    cfg = _cfg("supervisor")
    assert cfg["bot_peers"] == {f"peer-{r}": {"url": f"http://hermes-{r}:8642"} for r in SPECIALISTS}
    assert cfg["agent"].get("bot_mode_protocol", True) is True


@pytest.mark.parametrize("role", SPECIALISTS)
def test_specialists(role):
    cfg = _cfg(role)
    assert cfg["agent"]["bot_mode_protocol"] is False
    assert cfg["agent"]["run_budget_seconds"] == 180
    assert "bot_peers" not in cfg
    assert cfg["compression"]["threshold_tokens"] == 32000


def test_old_role_dirs_gone():
    assert not (HERMES / "edge").exists() and not (HERMES / "llm-edge").exists()
    assert not (HERMES / "config.yaml").exists()
