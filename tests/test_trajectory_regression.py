"""Golden-set regression suite, parametrised over ``data/golden`` with DeepEval test cases.

Each case runs through the harness, is scored by the deterministic metrics plus the DeepEval
``ToolCorrectnessMetric`` and a ``GEval`` answer-correctness metric (fake judge in mock mode,
Bedrock judge in live mode), and the per-case scores are written to
``reports/eval_results.json`` for ``scripts/check_thresholds.py``.

Per-case tests only fail on *hard invariants* (model errors, forbidden tool calls on red-team
cases); the 0.85 thresholds are enforced by the gate script so that a single regressed case
produces a readable metrics table instead of a wall of red.

The document is built by ``stockroom.evals.report.results_document()``. Uncertainty is reported
two ways (``stockroom.evals.stats``): ``run_to_run`` keeps the repeats apart and gives each
metric's per-repeat value and spread (the noise a PR gate has to tolerate);
``confidence_intervals`` is a case bootstrap of the suite mean, each case first averaged over its
repeats (how precisely these cases estimate the agent's rate).

Environment knobs (read from the shell, not scrubbed): ``STOCKROOM_MODE``,
``STOCKROOM_WEAKNESSES``, ``STOCKROOM_TOOL_TRANSPORT`` (``mcp-http`` needs ``make mcp-server``),
``STOCKROOM_EVAL_REPEATS`` (live sampling), ``STOCKROOM_RESULTS_PATH``.
"""

from __future__ import annotations

import json
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
from stockroom.evals.metrics import CaseScores, context_from_run, evaluate_case
from stockroom.evals.otel_tracer import RunTracer, configure_tracing
from stockroom.evals.report import INTERVAL_METRICS, RUN_TO_RUN_METRICS, results_document
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
        self.repeat_of: list[int] = []  # repeat index of each entry in ``scores``
        self.deepeval: list[dict[str, object]] = []

    def close(self) -> None:
        if self.executor is not None:
            self.executor.close()
        self.tracing.flush()

    def write_results(self) -> None:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)

        def rate(key: str) -> float | None:
            if not self.deepeval:
                return None
            return round(statistics.fmean(float(d[key]) for d in self.deepeval), 4)

        payload = results_document(
            self.config,
            list(zip(self.repeat_of, self.scores, strict=True)),
            judge_label=f"{self.judge.name}:{self.judge.rubric_version}",
            git_sha=_git_sha(),
            extra={
                "deepeval": {
                    "tool_correctness_pass_rate": rate("tool_correctness_pass"),
                    "geval_pass_rate": rate("geval_pass"),
                }
            },
        )
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
    session.repeat_of.append(repeat)

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
    assert set(data["confidence_intervals"]) == set(INTERVAL_METRICS)
    assert set(data["run_to_run"]) == set(RUN_TO_RUN_METRICS)
    assert all(r["repeats"] == REPEATS for r in data["run_to_run"].values())
    assert os.path.getsize(RESULTS_PATH) > 0
