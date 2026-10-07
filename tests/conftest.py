"""Shared fixtures. Every test runs offline in mock mode with an isolated environment."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from stockroom.agent.harness import Harness
from stockroom.config import StockroomConfig, TraceExporter
from stockroom.evals.golden import GoldenCase, load_calibration, load_golden
from stockroom.evals.judge import FakeJudge
from stockroom.evals.otel_tracer import RunTracer, TracingHandle, configure_tracing

_SCRUB_PREFIXES = ("STOCKROOM_", "AWS_", "AGENT_", "JUDGE_", "OTEL_", "MAX_", "TOKEN_")
# The shell environment as it was when pytest started; the regression suite reads its mode,
# weakness flags and transport from here even though unit tests run with a scrubbed env.
ORIGINAL_ENV: dict[str, str] = dict(os.environ)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not depend on the developer's shell (credentials, model IDs, weaknesses)."""
    for key in list(os.environ):
        if key.startswith(_SCRUB_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
    monkeypatch.setenv("STOCKROOM_MODE", "mock")


@pytest.fixture
def mock_config() -> StockroomConfig:
    return StockroomConfig.mock()


@pytest.fixture
def tracing() -> Iterator[TracingHandle]:
    handle = configure_tracing(TraceExporter.MEMORY)
    yield handle
    handle.clear()


@pytest.fixture
def harness(mock_config: StockroomConfig, tracing: TracingHandle) -> Harness:
    return Harness(mock_config, tracer=RunTracer(tracing))


@pytest.fixture(scope="session")
def golden_cases() -> list[GoldenCase]:
    return load_golden()


@pytest.fixture(scope="session")
def calibration_items():
    return load_calibration()


@pytest.fixture
def fake_judge() -> FakeJudge:
    return FakeJudge("v2")


def case_by_id(cases: list[GoldenCase], case_id: str) -> GoldenCase:
    return next(c for c in cases if c.id == case_id)
