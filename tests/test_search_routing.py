import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_web_search_returns_cited_sources():
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
