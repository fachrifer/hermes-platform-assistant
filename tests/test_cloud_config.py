from pathlib import Path


def test_cloud_compose_persists_required_paths():
    text = Path("docker-compose.cloud.yml").read_text()
    assert "/app/data" in text
    assert "/app/credentials" in text
    assert "restart: unless-stopped" in text
