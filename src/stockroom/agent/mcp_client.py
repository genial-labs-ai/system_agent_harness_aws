"""Connect the harness to the MCP inventory server (in-memory, stdio subprocess or HTTP).

The harness is synchronous, so :class:`McpToolExecutor` keeps one asyncio event loop on a
background thread and holds the ``mcp.Client`` session open for the executor's lifetime.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Coroutine
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.server import MCPServer

from stockroom.agent.types import ToolResult, ToolSpec
from stockroom.config import StockroomConfig, ToolTransport
from stockroom.mock_server.mcp_inventory_server import HIDDEN_TOOLS

_ENV_PASSTHROUGH = (
    "STOCKROOM_MODE",
    "STOCKROOM_WEAKNESSES",
    "STOCKROOM_DATA_DIR",
    "PYTHONPATH",
    "VIRTUAL_ENV",
)


class _LoopThread:
    """A dedicated event loop so sync code can await coroutines."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, name="mcp-client-loop", daemon=True
        )
        self.thread.start()

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float = 60.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


async def _wait(fut: asyncio.Future[None]) -> None:
    await fut


class McpToolExecutor:
    """``ToolExecutor`` implementation over an MCP client session."""

    def __init__(
        self,
        config: StockroomConfig,
        server: MCPServer | None = None,
        url: str | None = None,
        transport: ToolTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or config.tool_transport
        self._server = server
        self._url = url or config.mcp_url
        self._loop = _LoopThread()
        self._client: Client | None = None
        self._closing: asyncio.Event | None = None
        self._task: Any = None
        self._specs: list[ToolSpec] | None = None
        self.open()

    # -- lifecycle --------------------------------------------------------------------------------

    def _target(self) -> Any:
        if self._server is not None:
            return self._server
        if self.transport is ToolTransport.MCP_HTTP:
            return self._url
        env = {k: v for k, v in os.environ.items() if k in _ENV_PASSTHROUGH}
        env["STOCKROOM_WEAKNESSES"] = ",".join(sorted(self.config.weaknesses))
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "stockroom.mock_server.mcp_inventory_server", "--transport", "stdio"],
            env=env,
        )

    def open(self) -> None:
        """Enter the client session inside one long-lived task (anyio cancel scopes must be
        entered and exited by the same task)."""
        ready: asyncio.Future[None] = self._loop.loop.create_future()
        self._closing = asyncio.Event()

        async def _session() -> None:
            try:
                async with Client(self._target()) as client:
                    self._client = client
                    ready.set_result(None)
                    await self._closing.wait()
            except BaseException as exc:  # surface connection failures to the caller
                if not ready.done():
                    ready.set_exception(exc)
                raise
            finally:
                self._client = None

        self._task = asyncio.run_coroutine_threadsafe(_session(), self._loop.loop)
        asyncio.run_coroutine_threadsafe(_wait(ready), self._loop.loop).result(timeout=60)

    def close(self) -> None:
        try:
            if self._client is not None:
                self._loop.loop.call_soon_threadsafe(self._closing.set)
                self._task.result(timeout=30)
        finally:
            self._loop.stop()

    def __enter__(self) -> McpToolExecutor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- ToolExecutor -----------------------------------------------------------------------------

    def list_specs(self) -> list[ToolSpec]:
        if self._specs is None:
            assert self._client is not None
            result = self._loop.run(self._client.list_tools())
            self._specs = [
                ToolSpec(
                    name=t.name, description=t.description or "", input_schema=dict(t.input_schema)
                )
                for t in result.tools
                if t.name not in HIDDEN_TOOLS
            ]
        return list(self._specs)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        assert self._client is not None
        result = self._loop.run(self._client.call_tool(name, arguments))
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            code = "transient" if "transient" in text else "invalid_request"
            return ToolResult.error(
                name, text.split(": ", 1)[-1] if ": " in text else text, code=code
            )
        content: Any = result.structured_content
        if content is None:
            try:
                content = json.loads(text)
            except ValueError:
                content = text
        if isinstance(content, dict) and set(content) == {"result"}:
            content = content["result"]
        return ToolResult.ok(name, content)

    def reset(self) -> None:
        assert self._client is not None
        self._loop.run(self._client.call_tool("reset_session_state", {}))
