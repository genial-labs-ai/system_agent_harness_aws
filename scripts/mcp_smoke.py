#!/usr/bin/env python
"""Smoke-test a running MCP inventory server: list tools and call one."""

from __future__ import annotations

import argparse
import asyncio

from mcp import Client


async def smoke(url: str) -> int:
    async with Client(url) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools.tools]
        print(f"connected to {url}: {len(names)} tools -> {names}")
        result = await client.call_tool("get_stock_level", {"sku": "SKU-1015"})
        print(
            "get_stock_level(SKU-1015) ->",
            result.structured_content or [c.text for c in result.content],
        )
        expected = {
            "search_products",
            "get_stock_level",
            "get_order_status",
            "create_restock_request",
            "search_policy_docs",
        }
        missing = expected - set(names)
        if missing or result.is_error:
            print(f"FAIL: missing={sorted(missing)} is_error={result.is_error}")
            return 1
    print("OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    args = parser.parse_args()
    return asyncio.run(smoke(args.url))


if __name__ == "__main__":
    raise SystemExit(main())
