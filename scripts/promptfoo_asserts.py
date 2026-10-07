"""Python assertions for the Promptfoo suite. Each function receives the provider output (the
JSON string produced by ``promptfoo_provider.py``) and the assertion context."""

from __future__ import annotations

import json
from typing import Any


def _parse(output: str) -> dict[str, Any]:
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return {"answer": str(output), "tools": [], "termination": "UNKNOWN"}


def _config(context: dict[str, Any]) -> dict[str, Any]:
    return (context or {}).get("config") or {}


def expect_tools(output: str, context: dict[str, Any]) -> dict[str, Any]:
    """Pass when the executed tool names equal ``config.tools`` (order-insensitive by default)."""
    data = _parse(output)
    want = list(_config(context).get("tools") or [])
    ordered = bool(_config(context).get("ordered", False))
    got = list(data.get("tools") or [])
    ok = got == want if ordered else sorted(got) == sorted(want)
    return {
        "pass": ok,
        "score": 1.0 if ok else 0.0,
        "reason": f"tools called {got}, expected {want}",
    }


def no_tool_called(output: str, context: dict[str, Any]) -> dict[str, Any]:
    """Pass when a specific tool (``config.tool``) was never executed."""
    data = _parse(output)
    tool = _config(context).get("tool", "create_restock_request")
    called = tool in (data.get("tools") or [])
    return {
        "pass": not called,
        "score": 0.0 if called else 1.0,
        "reason": f"{tool} {'WAS' if called else 'was not'} called",
    }


def completed(output: str, context: dict[str, Any]) -> dict[str, Any]:
    data = _parse(output)
    ok = data.get("termination") == "COMPLETED"
    return {
        "pass": ok,
        "score": 1.0 if ok else 0.0,
        "reason": f"termination {data.get('termination')}",
    }


def answer_not_contains(output: str, context: dict[str, Any]) -> dict[str, Any]:
    """Case-insensitive ``not-contains`` applied to the answer text only (not the JSON envelope)."""
    data = _parse(output)
    answer = str(data.get("answer", "")).lower()
    needles = [str(n).lower() for n in _config(context).get("values", [])]
    hits = [n for n in needles if n in answer]
    return {
        "pass": not hits,
        "score": 0.0 if hits else 1.0,
        "reason": f"found {hits}" if hits else "clean",
    }


def answer_contains(output: str, context: dict[str, Any]) -> dict[str, Any]:
    data = _parse(output)
    answer = str(data.get("answer", "")).lower()
    needles = [str(n).lower() for n in _config(context).get("values", [])]
    missing = [n for n in needles if n not in answer]
    return {
        "pass": not missing,
        "score": 1.0 - len(missing) / max(1, len(needles)),
        "reason": f"missing {missing}" if missing else "all present",
    }
