"""Golden-set regression suite, parametrised over ``data/golden`` with DeepEval test cases.

Each case runs through the harness, is scored by the deterministic metrics plus the DeepEval
``ToolCorrectnessMetric`` and a ``GEval`` answer-correctness metric (fake judge in mock mode,
Bedrock judge in live mode), and the per-case scores are written to
``reports/eval_results.json`` for ``scripts/check_thresholds.py``.

Per-case tests only fail on *hard invariants* (model errors, forbidden tool calls on red-team
cases); the 0.85 thresholds are enforced by the gate script so that a single regressed case
produces a readable metrics table instead of a wall of red.

Environment knobs (read from the shell, not scrubbed): ``STOCKROOM_MODE``,
``STOCKROOM_WEAKNESSES``, ``STOCKROOM_TOOL_TRANSPORT`` (``mcp-http`` needs ``make mcp-server``),
``STOCKROOM_EVAL_REPEATS`` (live sampling), ``STOCKROOM_RESULTS_PATH``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from stockroom.agent.harness import Harness
from stockroom.agent.mcp_client import McpToolExecutor
from stockroom.agent.types import TerminationReason
from stockroom.config import REPO_ROOT, StockroomConfig, ToolTransport
from stockroom.evals.golden import GoldenCase, load_golden
from stockroom.evals.judge import StockroomDeepEvalLLM, load_rubric, make_judge
from stockroom.evals.metrics import CaseScores, aggregate, context_from_run, evaluate_case
from stockroom.evals.otel_tracer import RunTracer, configure_tracing
from tests.conftest import ORIGINAL_ENV

RESULTS_PATH = Path(
    ORIGINAL_ENV.get("STOCKROOM_RESULTS_PATH", REPO_ROOT / "reports" / "eval_results.json")
)
REPEATS = max(1, int(ORIGINAL_ENV.get("STOCKROOM_EVAL_REPEATS", "1")))
CASES = load_golden()


def _git_sha() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            ).stdout.strip()
            or None
        )
    except OSError:
        return None


def _confidence_interval(values: list[float]) -> dict[str, float]:
    n = len(values)
    mean = statistics.fmean(values) if values else 0.0
    sd = statistics.pstdev(values) if n > 1 else 0.0
    half = 1.96 * sd / math.sqrt(n) if n > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "ci95_low": round(mean - half, 4),
        "ci95_high": round(mean + half, 4),
        "n": n,
    }


class Session:
    """Holds the harness, judge and collected scores for the whole pytest session."""

    def __init__(self) -> None:
        self.config = StockroomConfig.from_env(ORIGINAL_ENV)
        self.tracing = configure_tracing(self.config.trace_exporter)
        self.executor = None
        if self.config.tool_transport in (ToolTransport.MCP_HTTP, ToolTransport.MCP_STDIO):
            self.executor = McpToolExecutor(self.config)
        self.harness = Harness(self.config, executor=self.executor, tracer=RunTracer(self.tracing))
        self.judge = make_judge(self.config)
        self.deepeval_llm = StockroomDeepEvalLLM(self.config, self.judge)
        self.scores: list[CaseScores] = []
        self.deepeval: list[dict[str, object]] = []

    def close(self) -> None:
        if self.executor is not None:
            self.executor.close()
        self.tracing.flush()

    def write_results(self) -> None:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = self.config.data_dir / "golden" / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        metrics = aggregate(self.scores)
        by_metric = {
            name: _confidence_interval(
                [
                    getattr(s, attr) if attr != "judge_passed" else float(bool(s.judge_passed))
                    for s in self.scores
                ]
            )
            for name, attr in (
                ("tool_selection_accuracy", "tool_selection"),
                ("answer_correctness", "judge_passed"),
                ("argument_correctness", "argument_correctness"),
            )
        }
        payload = {
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "mode": self.config.mode.value,
            "weaknesses": sorted(self.config.weaknesses),
            "tool_transport": self.config.tool_transport.value,
            "repeats": REPEATS,
            "git_sha": _git_sha(),
            "agent_model_id": self.config.agent_model_id
            if self.config.is_live
            else "fake.stockroom-planner-v1",
            "judge": f"{self.judge.name}:{self.judge.rubric_version}",
            "dataset": {k: manifest.get(k) for k in ("name", "version", "sha256", "cases")},
            "metrics": metrics,
            "confidence_intervals": by_metric,
            "deepeval": {
                "tool_correctness_pass_rate": round(
                    statistics.fmean([float(d["tool_correctness_pass"]) for d in self.deepeval]), 4
                )
                if self.deepeval
                else None,
                "geval_pass_rate": round(
                    statistics.fmean([float(d["geval_pass"]) for d in self.deepeval]), 4
                )
                if self.deepeval
                else None,
            },
            "cases": [s.model_dump(mode="json") for s in self.scores],
        }
        RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    sess = Session()
    yield sess
    sess.write_results()
    sess.close()


def _geval(llm: StockroomDeepEvalLLM) -> GEval:
    return GEval(
        name="Answer correctness (rubric v2)",
        criteria=load_rubric("answer_correctness", "v2"),
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=llm,
        threshold=0.7,
        async_mode=False,
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
@pytest.mark.parametrize("repeat", range(REPEATS), ids=[f"r{i}" for i in range(REPEATS)])
def test_golden_case(session: Session, case: GoldenCase, repeat: int) -> None:
    run = session.harness.run(case.query, case_id=case.id)
    scores = evaluate_case(case, run, session.judge, window=session.config.repeat_call_window)
    session.scores.append(scores)

    test_case = LLMTestCase(
        input=case.query,
        actual_output=run.final_answer,
        expected_output=case.reference_answer,
        context=[context_from_run(run)] if run.executed_tool_calls else None,
        tools_called=[
            ToolCall(name=r.name, input_parameters=r.arguments) for r in run.executed_tool_calls
        ],
        expected_tools=[
            ToolCall(name=t.name, input_parameters=t.args) for t in case.expected_tools
        ],
        name=f"{case.id}-r{repeat}",
    )
    tool_metric = ToolCorrectnessMetric(
        model=session.deepeval_llm, should_consider_ordering=case.trajectory_match_mode == "exact"
    )
    tool_metric.measure(test_case)
    geval = _geval(session.deepeval_llm)
    geval.measure(test_case)
    session.deepeval.append(
        {
            "case_id": case.id,
            "tool_correctness_pass": bool(tool_metric.is_successful()),
            "tool_correctness_score": tool_metric.score,
            "geval_pass": bool(geval.is_successful()),
            "geval_score": geval.score,
        }
    )

    # Hard invariants only (soft metrics are gated by scripts/check_thresholds.py).
    assert run.termination_reason is not TerminationReason.MODEL_ERROR, run.final_answer
    if "red_team" in case.tags or case.category == "injection":
        assert scores.must_not_call_ok, f"{case.id}: forbidden tool executed {run.tool_names}"
        assert not scores.forbidden_found, f"{case.id}: leaked {scores.forbidden_found}"


def test_results_file_is_written(session: Session) -> None:
    assert session.scores, "no cases were scored"
    session.write_results()
    data = json.loads(RESULTS_PATH.read_text())
    assert data["metrics"]["cases"] == len(session.scores)
    assert set(data["confidence_intervals"]) == {
        "tool_selection_accuracy",
        "answer_correctness",
        "argument_correctness",
    }
    assert os.path.getsize(RESULTS_PATH) > 0
