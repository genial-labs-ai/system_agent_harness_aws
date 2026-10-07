from __future__ import annotations

import pytest

from stockroom.config import ConfigError, Mode, StockroomConfig, detect_mode, parse_weaknesses
from stockroom.cost import PriceTable, canonical_model_id


def test_defaults_are_mock_and_offline() -> None:
    cfg = StockroomConfig.from_env({})
    assert cfg.mode is Mode.MOCK
    assert cfg.weaknesses == frozenset()
    assert cfg.agent_model_id is None
    assert "FakeBedrockClient" in cfg.describe()


def test_env_parsing_and_validation() -> None:
    cfg = StockroomConfig.from_env(
        {
            "STOCKROOM_MODE": "live",
            "AGENT_MODEL_ID": "us.example.agent-v1:0",
            "JUDGE_MODEL_ID": "us.example.judge-v1:0",
            "AWS_REGION": "eu-west-1",
            "MAX_STEPS": "5",
            "TOKEN_BUDGET": "9000",
            "STOCKROOM_WEAKNESSES": "naive_retry, oversized_payload",
        }
    )
    assert cfg.is_live and cfg.aws_region == "eu-west-1" and cfg.max_steps == 5
    assert cfg.weaknesses == {"naive_retry", "oversized_payload"}
    assert cfg.require_live_models() == ("us.example.agent-v1:0", "us.example.judge-v1:0")


def test_live_without_model_ids_is_a_clear_error() -> None:
    cfg = StockroomConfig.from_env({"STOCKROOM_MODE": "live"})
    with pytest.raises(ConfigError, match="AGENT_MODEL_ID and JUDGE_MODEL_ID"):
        cfg.require_live_models()


@pytest.mark.parametrize(
    "env",
    [
        {"STOCKROOM_MODE": "sometimes"},
        {"STOCKROOM_WEAKNESSES": "lazy_model"},
        {"MAX_STEPS": "0"},
        {"TOKEN_BUDGET": "10", "MAX_TOKENS": "100"},
        {"REPEAT_CALL_WINDOW": "1"},
    ],
)
def test_invalid_env_raises(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        StockroomConfig.from_env(env)


def test_parse_weaknesses_accepts_iterables() -> None:
    assert parse_weaknesses(["naive_retry"]) == {"naive_retry"}
    assert parse_weaknesses(None) == frozenset()
    with pytest.raises(ConfigError):
        parse_weaknesses("nope")


def test_detect_mode_falls_back_to_mock_without_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = detect_mode({"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""})
    assert cfg.mode is Mode.MOCK
    assert "MOCK mode" in capsys.readouterr().out


def test_price_table_known_and_unknown(mock_config: StockroomConfig) -> None:
    table = PriceTable.load(mock_config.pricing_file)
    nova = table.estimate("us.amazon.nova-pro-v1:0", 1000, 1000)
    assert nova.known and nova.usd is not None and nova.usd > 0
    claude = table.estimate("us.anthropic.claude-haiku-4-5-20251001-v1:0", 1000, 1000)
    assert not claude.known and "unknown" in claude.format()
    override = table.estimate("anything", 1000, 0, input_override=0.001, output_override=0.002)
    assert override.usd == pytest.approx(0.001)
    assert canonical_model_id("global.anthropic.claude-x") == "anthropic.claude-x"
