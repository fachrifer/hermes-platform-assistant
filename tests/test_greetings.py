import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_short_greeting_gets_a_concise_nyx_response_without_llm():
    agent = HermesAgent.__new__(HermesAgent)
    agent.memory = type("Memory", (), {
        "add": lambda *args: None,
        "recent": lambda *args: [],
    })()

    class UnexpectedLLM:
        async def generate(self, *args, **kwargs):
            raise AssertionError("sapaan singkat tidak perlu dikirim ke Gemini")

    agent.llm = UnexpectedLLM()

    result = await agent.handle_message("chat", "hi", plan={"action": "chat", "args": {}})

    assert result == "Halo, Master. Nyx aktif. Ada yang bisa saya bantu?"
