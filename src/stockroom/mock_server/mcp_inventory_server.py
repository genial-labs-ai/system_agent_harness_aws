"""MCP inventory server built on the official ``mcp`` SDK (v2 ``MCPServer``).

It exposes the same five tools as ``stockroom.agent.tools`` so the harness can be pointed at a
real process boundary. Run it with::

    python -m stockroom.mock_server.mcp_inventory_server                      # stdio
    python -m stockroom.mock_server.mcp_inventory_server --transport streamable-http --port 8765

Weakness flags are read from ``STOCKROOM_WEAKNESSES`` so the Day 3 lab can toggle them for the
server process as well. A sixth, hidden ``reset_session_state`` tool lets the harness reset the
per-run ledger; it is filtered out of the tool list shown to the model.
"""

from __future__ import annotations

import argparse
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from stockroom.agent.tools import StockroomTools
from stockroom.config import StockroomConfig

HIDDEN_TOOLS = {"reset_session_state"}


def build_server(config: StockroomConfig | None = None) -> MCPServer:
    config = config or StockroomConfig.from_env()
    tools = StockroomTools(config)
    spec = {s.name: s for s in tools.specs}
    mcp = MCPServer(
        "stockroom-inventory",
        instructions="Inventory, order and policy tools for the Stockroom support agent.",
        version="0.1.0",
        log_level="WARNING",
    )

    def unwrap(result: Any) -> Any:
        if result.is_error:
            raise ToolError(f"{result.error_code}: {result.content.get('message', '')}")
        return result.content

    @mcp.tool(name="search_products", description=spec["search_products"].description)
    def search_products(
        query: Annotated[str, Field(min_length=1, description="Name, category or keyword")],
        max_results: Annotated[int | None, Field(ge=1, le=20)] = None,
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"query": query}
        if max_results is not None:
            args["max_results"] = max_results
        return unwrap(tools.call("search_products", args))

    @mcp.tool(name="get_stock_level", description=spec["get_stock_level"].description)
    def get_stock_level(
        sku: Annotated[str, Field(pattern=r"^SKU-\d{4}$", description="SKU-####")],
    ) -> dict[str, Any]:
        return unwrap(tools.call("get_stock_level", {"sku": sku}))

    @mcp.tool(name="get_order_status", description=spec["get_order_status"].description)
    def get_order_status(
        order_id: Annotated[str, Field(pattern=r"^ORD-\d{4}$", description="ORD-####")],
    ) -> dict[str, Any]:
        return unwrap(tools.call("get_order_status", {"order_id": order_id}))

    @mcp.tool(name="create_restock_request", description=spec["create_restock_request"].description)
    def create_restock_request(
        sku: Annotated[str, Field(pattern=r"^SKU-\d{4}$")],
        quantity: Annotated[int, Field(ge=1)],
        priority: Annotated[str, Field(pattern="^(standard|urgent)$")] = "standard",
    ) -> dict[str, Any]:
        return unwrap(
            tools.call(
                "create_restock_request",
                {"sku": sku, "quantity": quantity, "priority": priority},
            )
        )

    @mcp.tool(name="search_policy_docs", description=spec["search_policy_docs"].description)
    def search_policy_docs(
        query: Annotated[str, Field(min_length=1)],
        top_k: Annotated[int, Field(ge=1, le=5)] = 3,
    ) -> list[dict[str, Any]]:
        return unwrap(tools.call("search_policy_docs", {"query": query, "top_k": top_k}))

    @mcp.tool(
        name="reset_session_state",
        description="Harness-only: reset the restock ledger and legacy-shard counters.",
    )
    def reset_session_state() -> dict[str, str]:
        tools.reset()
        return {"status": "reset"}

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
