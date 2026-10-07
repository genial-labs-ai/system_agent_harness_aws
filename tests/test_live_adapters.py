"""Live-mode code paths exercised with stubbed boto3 clients (no network, no credentials)."""

from __future__ import annotations

from typing import Any

import pytest

from stockroom.agent.bedrock_adapter import (
    BedrockConverseClient,
    ModelError,
    make_model_client,
    parse_converse_response,
)
from stockroom.agent.harness import Harness
from stockroom.agent.types import TerminationReason, ToolSpec
from stockroom.config import Mode, StockroomConfig
from stockroom.evals.judge import BedrockJudge, JudgeInput, StockroomDeepEvalLLM, make_judge

LIVE_ENV = {
    "STOCKROOM_MODE": "live",
    "AGENT_MODEL_ID": "us.example.agent-model-v1:0",
    "JUDGE_MODEL_ID": "us.example.judge-model-v1:0",
    "AWS_REGION": "us-east-1",
    "AGENT_PRICE_INPUT_PER_1K": "0.001",
    "AGENT_PRICE_OUTPUT_PER_1K": "0.002",
}


class StubRuntime:
    """Scripted ``bedrock-runtime.converse`` responses in the real Converse shape."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("no scripted response left")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def tool_use(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tu-1", "name": name, "input": args}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 300, "outputTokens": 20, "totalTokens": 320},
        "metrics": {"latencyMs": 412},
    }


def text(answer: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": answer}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 500, "outputTokens": 40, "totalTokens": 540},
        "metrics": {"latencyMs": 900},
    }


def test_parse_converse_response() -> None:
    parsed = parse_converse_response(tool_use("get_stock_level", {"sku": "SKU-1015"}), "m")
    assert parsed.tool_calls[0].name == "get_stock_level" and parsed.stop_reason == "tool_use"
    assert parsed.input_tokens == 300 and parsed.latency_ms == 412 and parsed.text is None


def test_live_harness_round_trip_with_stubbed_bedrock(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = StockroomConfig.from_env(LIVE_ENV)
    stub = StubRuntime(
        [tool_use("get_stock_level", {"sku": "SKU-1015"}), text("SKU-1015 has 42 units in stock.")]
    )
    client = BedrockConverseClient(cfg, cfg.agent_model_id or "", client=stub)
    result = Harness(cfg, model_client=client).run("How many units of SKU-1015 are in stock?")
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.tool_names == ["get_stock_level"] and "42" in result.final_answer
    # Request shape: system block, maxTokens enforced, toolConfig present, tool result as user turn.
    first, second = stub.requests
    assert first["modelId"] == "us.example.agent-model-v1:0"
    assert first["system"] == [{"text": Harness(cfg, model_client=client).system_prompt}]
    assert first["inferenceConfig"]["maxTokens"] == cfg.max_tokens
    assert {t["toolSpec"]["name"] for t in first["toolConfig"]["tools"]} >= {
        "get_stock_level",
        "search_products",
    }
    assert (
        second["messages"][-1]["role"] == "user"
        and "toolResult" in second["messages"][-1]["content"][0]
    )
    assert second["messages"][-1]["content"][0]["toolResult"]["status"] == "success"
    # Usage and cost come from the API response and the env price override.
    assert result.usage.input_tokens == 800 and result.usage.output_tokens == 60
    assert result.cost_estimate.usd == pytest.approx(0.8 * 0.001 + 0.06 * 0.002)
    assert "estimated cost" in capsys.readouterr().out


def test_live_model_errors_are_typed() -> None:
    from botocore.exceptions import ClientError

    cfg = StockroomConfig.from_env(LIVE_ENV)
    throttle = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse"
    )
    client = BedrockConverseClient(cfg, "m", client=StubRuntime([throttle]))
    with pytest.raises(ModelError) as exc:
        client.converse(
            "sys",
            [{"role": "user", "content": [{"text": "hi"}]}],
            [ToolSpec(name="t", description="d", input_schema={"type": "object"})],
            10,
        )
    assert exc.value.kind == "throttled"
    result = Harness(
        cfg, model_client=BedrockConverseClient(cfg, "m", client=StubRuntime([throttle]))
    ).run("hi")
    assert result.termination_reason is TerminationReason.MODEL_ERROR


def test_make_model_client_and_judge_follow_mode() -> None:
    assert make_model_client(StockroomConfig.mock()).model_id == "fake.stockroom-planner-v1"
    cfg = StockroomConfig.from_env(LIVE_ENV)
    assert cfg.mode is Mode.LIVE
    stub = StubRuntime([text('{"score": 9, "pass": true, "rationale": "all facts present"}')])
    judge = BedrockJudge(cfg, client=stub)
    verdict = judge.grade(JudgeInput(query="q", answer="42 units", reference="42 units in stock"))
    assert (
        verdict.passed
        and verdict.score == pytest.approx(0.9)
        and verdict.judge_name.endswith("us.example.judge-model-v1:0")
    )
    assert stub.requests[0]["modelId"] == "us.example.judge-model-v1:0"
    assert (
        "Rubric: answer correctness (v2)" in stub.requests[0]["messages"][0]["content"][0]["text"]
    )
    assert make_judge(StockroomConfig.mock()).name == "fake-judge"


def test_deepeval_bridge_in_live_mode_parses_schema() -> None:
    from pydantic import BaseModel

    class ReasonScore(BaseModel):
        reason: str
        score: float

    cfg = StockroomConfig.from_env(LIVE_ENV)
    stub = StubRuntime([text('```json\n{"reason": "ok", "score": 8}\n```')])
    llm = StockroomDeepEvalLLM(cfg, BedrockJudge(cfg, client=stub))
    out = llm.generate("Evaluate…", ReasonScore)
    assert isinstance(out, ReasonScore) and out.score == 8
    assert (
        "JSON object matching this schema" in stub.requests[0]["messages"][0]["content"][0]["text"]
    )


def test_sync_script_dry_runs_without_aws(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    from stockroom.config import REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "sync_datasets_s3", REPO_ROOT / "scripts" / "sync_datasets_s3.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key, value in {**LIVE_ENV, "S3_BUCKET": "example-bucket", "S3_PREFIX": "workshop"}.items():
        monkeypatch.setenv(key, value)
    assert module.main(["upload-golden", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "s3://example-bucket/workshop/golden/1.0.0/stockroom_golden_v1.jsonl" in out
    monkeypatch.delenv("S3_BUCKET")
    assert module.main(["upload-golden", "--dry-run"]) == 2
