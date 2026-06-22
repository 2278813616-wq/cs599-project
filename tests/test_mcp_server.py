import json

import pytest

import src.mcp_server as mcp_server


@pytest.mark.asyncio
async def test_mcp_initialize_list_and_call(monkeypatch):
    class DummyRetriever:
        def query_diet_safety(self, disease, food):
            return {
                "disease": disease,
                "food": food,
                "safe": True,
                "reason": "pytest deterministic MCP smoke result",
            }

    monkeypatch.setattr(mcp_server, "retriever", DummyRetriever())

    init = await mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "superfoodie-mcp-server"

    tools = await mcp_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "query_diet_safety" in names

    called = await mcp_server.handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "query_diet_safety",
            "arguments": {"disease": "gout", "food": "seafood"},
        },
    })
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["safe"] is True
    assert payload["food"] == "seafood"
