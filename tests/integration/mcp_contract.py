"""Run inside nousresearch/hermes-agent:v2026.9.21 against a running gateway.

Usage: /opt/hermes/.venv/bin/python mcp_contract.py <url> <token> <expected_tool> <call_tool>
"""
import asyncio
import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str, token: str, expected: str, call: str) -> int:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=15) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print("protocol", getattr(init, "protocol_version", None) or getattr(init, "protocolVersion", None))
                listed = await session.list_tools()
                names = sorted(t.name for t in listed.tools)
                print("tools", names)
                assert expected in names, names
                result = await session.call_tool(call, {})
                envelope = json.loads(result.content[0].text)
                print("call", call, "ok" if envelope["ok"] else envelope["error"])
                assert "ok" in envelope
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:5])))
