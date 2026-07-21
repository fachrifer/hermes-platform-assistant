import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_natural_delete_is_parsed_without_gemini():
    agent = HermesAgent.__new__(HermesAgent)

    class LLM:
        async def plan(self, *args):
            raise AssertionError("delete agenda harus diproses deterministik")

    agent.llm = LLM()

    result = await agent.plan_message("hapus agenda nomor 3")

    assert result == {"action": "agenda_delete", "args": {"id": 3}}
