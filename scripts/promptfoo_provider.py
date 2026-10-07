"""Promptfoo custom Python provider that runs a query through the Stockroom harness.

Promptfoo calls ``call_api(prompt, options, context)`` once per test; the returned ``output`` is a
JSON string ``{"answer", "tools", "termination", "quarantined"}`` so assertions can check both the
text and the trajectory. Mode and weakness flags come from the environment (``STOCKROOM_MODE``,
``STOCKROOM_WEAKNESSES``), exactly like the pytest suite.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from stockroom.agent.harness import Harness  # noqa: E402
from stockroom.config import StockroomConfig  # noqa: E402

_HARNESS: Harness | None = None


def _harness() -> Harness:
    global _HARNESS
    if _HARNESS is None:
        _HARNESS = Harness(StockroomConfig.from_env(os.environ))
    return _HARNESS


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    test_meta = ((context or {}).get("test") or {}).get("metadata") or {}
    case_id = test_meta.get("case_id") if str(test_meta.get("suite", "")) == "golden" else None
    try:
        result = _harness().run(prompt, case_id=case_id)
    except Exception as exc:  # the provider must never crash the whole eval
        return {"error": f"{type(exc).__name__}: {exc}"}
    payload = {
        "answer": result.final_answer,
        "tools": result.tool_names,
        "termination": result.termination_reason.value,
        "quarantined": sum(r.quarantined_lines for r in result.tool_records),
        "steps": result.usage.model_calls,
    }
    return {
        "output": json.dumps(payload, ensure_ascii=False),
        "tokenUsage": {
            "total": result.usage.total_tokens,
            "prompt": result.usage.input_tokens,
            "completion": result.usage.output_tokens,
        },
        "cost": result.cost_estimate.usd or 0.0,
        "latencyMs": int(result.duration_ms),
    }
