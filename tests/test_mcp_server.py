from __future__ import annotations

import pytest
from mcp import Client

from stockroom.agent.harness import Harness
from stockroom.agent.mcp_client import McpToolExecutor
from stockroom.config import StockroomConfig, ToolTransport
from stockroom.mock_server.mcp_inventory_server import HIDDEN_TOOLS, build_server

VISIBLE = {
    "search_products",
    "get_stock_level",
    "get_order_status",
    "create_restock_request",
    "search_policy_docs",
}


@pytest.mark.anyio
async def test_server_lists_and_calls_tools_in_memory(mock_config: StockroomConfig) -> None:
    async with Client(build_server(mock_config)) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == VISIBLE | HIDDEN_TOOLS
        ok = await client.call_tool("get_stock_level", {"sku": "SKU-1015"})
        assert not ok.is_error and ok.structured_content["stock_level"] == 42
        bad = await client.call_tool("create_restock_request", {"sku": "SKU-1020", "quantity": 5})
        assert bad.is_error and "minimum" in bad.content[0].text
        schema = next(t for t in tools.tools if t.name == "get_order_status").input_schema
        assert schema["properties"]["order_id"]["pattern"].startswith("^ORD-")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_executor_in_memory_through_harness(mock_config: StockroomConfig) -> None:
    with McpToolExecutor(mock_config, server=build_server(mock_config)) as executor:
        assert {s.name for s in executor.list_specs()} == VISIBLE
        harness = Harness(mock_config, executor=executor)
        first = harness.run("Please raise a restock request for 50 units of SKU-1007.")
        second = harness.run("Please raise a restock request for 50 units of SKU-1007.")
        assert (
            "RSR-0001" in first.final_answer and "RSR-0001" in second.final_answer
        )  # reset per run
        transient = executor.call("get_order_status", {"order_id": "ORD-9001"})
        assert not transient.is_error


def test_executor_over_stdio_subprocess(mock_config: StockroomConfig) -> None:
    cfg = mock_config.replace(
        tool_transport=ToolTransport.MCP_STDIO, weaknesses="ambiguous_tool_desc"
    )
    with McpToolExecutor(cfg) as executor:
        specs = {s.name: s for s in executor.list_specs()}
        assert set(specs) == VISIBLE
        assert "orders" in specs["search_products"].description  # weakness flag reached the child
        # The harness advertises whatever the server describes, so the ambiguous description
        # captures the order lookup even though the harness config itself has no weakness.
        result = Harness(mock_config, executor=executor).run("What's the status of order ORD-1001?")
        assert result.tool_names == ["search_products"]
    with McpToolExecutor(mock_config.replace(tool_transport=ToolTransport.MCP_STDIO)) as executor:
        result = Harness(mock_config, executor=executor).run("What's the status of order ORD-1001?")
        assert result.tool_names == ["get_order_status"] and "DP48213377" in result.final_answer
