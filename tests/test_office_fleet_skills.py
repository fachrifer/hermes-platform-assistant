import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant" / "skills"
ROLES = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
BANNED = re.compile(r"curl|office-gw|terminal|AUTOHEAL|auto_execute|a2a_call|kanban|OFFICE_GATEWAY_URL|SNAPSHOT:|FAST:", re.I)
TOOLS = {
    "supervisor": ["fleet_status", "message_agent"],
    "lab-host": ["list_containers", "inspect_container", "tail_logs", "host_resources", "list_host_services", "propose_restart", "action_status"],
    "ingress": ["list_routes", "edge_status", "tls_status", "tail_traefik_logs", "validate_route_change", "propose_route_change", "propose_route_rollback", "action_status"],
    "llm": ["llm_status", "list_models"],
    "cluster-gpu": ["k8s_get", "mig_map", "gpu_usage"],
    "vector": ["vector_status", "milvus_databases", "milvus_collections", "milvus_collection", "milvus_users", "milvus_roles"],
    "obs": ["metrics_query", "grafana_links"],
}


def _text(role):
    return (SKILLS / role / "SKILL.md").read_text(encoding="utf-8")


def test_exactly_seven_skill_dirs():
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == sorted(ROLES)


@pytest.mark.parametrize("role", ROLES)
def test_frontmatter_size_and_banned_words(role):
    text = _text(role)
    assert text.startswith("---\n")
    front = text.split("---\n", 2)[1]
    assert f"name: office-{role}" in front
    assert "description:" in front
    assert len(text) <= 6000, len(text)
    assert not BANNED.search(text), BANNED.search(text).group(0)


@pytest.mark.parametrize("role", ROLES)
def test_mentions_every_tool(role):
    text = _text(role)
    for tool in TOOLS[role]:
        assert tool in text, tool


def test_obs_skill_lists_every_named_query():
    from office_gateway.reports import queries

    text = _text("obs")
    for name in queries.QUERIES:
        assert f"`{name}`" in text, name


def test_supervisor_routing_and_rules():
    text = _text("supervisor")
    for role in ROLES[1:]:
        assert f"peer-{role}" in text
    assert "at most two" in text.lower() and "never re-send" in text.lower()
    assert "/approvals/" in text
    assert "buka sesi **Bot Chat**" in text and "agent ok" in text


@pytest.mark.parametrize("role", ROLES[1:])
def test_specialist_reply_format(role):
    text = _text(role)
    for field in ("STATUS:", "FINDINGS:", "CAUSE:", "NEXT:"):
        assert field in text
