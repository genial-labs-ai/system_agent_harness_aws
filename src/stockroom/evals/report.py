"""The results document the regression suite writes and the gate reads (``eval_results.json``).

One builder, :func:`results_document`, is shared by ``tests/test_trajectory_regression.py`` (which
writes ``reports/eval_results.json``), the Day 4 notebook and the seeded-weakness gate test, so
``scripts/check_thresholds.py`` always sees the same shape: provenance (mode, agent model, judge,
dataset hash, repeats), the ``aggregate()`` metrics, the uncertainty blocks from
``stockroom.evals.stats`` and every per-case score.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from stockroom.config import StockroomConfig
from stockroom.evals.metrics import CaseScores, aggregate
from stockroom.evals.stats import answer_value, case_bootstrap_interval, run_to_run

MOCK_AGENT_MODEL_ID = "fake.stockroom-planner-v1"
RUN_TO_RUN_METRICS = (
    "tool_selection_accuracy",
    "answer_correctness",
    "argument_correctness",
    "termination_match_rate",
)
INTERVAL_METRICS = ("tool_selection_accuracy", "answer_correctness", "argument_correctness")
RESERVED_KEYS = frozenset(
    {
        "generated_at",
        "mode",
        "weaknesses",
        "tool_transport",
        "repeats",
        "git_sha",
        "agent_model_id",
        "judge",
        "dataset",
        "metrics",
        "run_to_run",
        "confidence_intervals",
        "cases",
    }
)


def case_value(score: CaseScores, metric: str) -> float:
    """One case's contribution to ``metric``, as ``aggregate()`` counts it.

    For answer correctness, ``aggregate()`` skips unjudged cases once any case is judged; the
    interval in :func:`uncertainty` drops them the same way.
    """
    if metric == "tool_selection_accuracy":
        return score.tool_selection
    if metric == "argument_correctness":
        return score.argument_correctness
    if metric == "answer_correctness":
        return answer_value(score.judge_passed, score.answer_correctness_deterministic)
    raise ValueError(f"no per-case value for {metric}")


def uncertainty(
    scored: Sequence[tuple[int, CaseScores]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """``(run_to_run, confidence_intervals)`` from ``(repeat index, scores)`` pairs."""
    if not scored:
        return {}, {}
    repeats = sorted({r for r, _ in scored})
    per_repeat = [aggregate([s for r, s in scored if r == rep]) for rep in repeats]
    spread = {m: run_to_run([agg[m] for agg in per_repeat]) for m in RUN_TO_RUN_METRICS}
    by_case: dict[str, list[CaseScores]] = {}
    for _, s in scored:
        by_case.setdefault(s.case_id, []).append(s)
    intervals = {
        m: case_bootstrap_interval(
            [statistics.fmean(case_value(s, m) for s in rows) for rows in _rows_for(m, by_case)]
        )
        for m in INTERVAL_METRICS
    }
    return spread, intervals


def _rows_for(metric: str, by_case: dict[str, list[CaseScores]]) -> list[list[CaseScores]]:
    """Each case's rows that ``aggregate()`` counts towards ``metric``.

    Once any case has a judge verdict, ``aggregate()`` averages answer correctness over judged
    rows only, so the interval drops unjudged rows too and stays centred on the reported metric.
    """
    groups = list(by_case.values())
    if metric != "answer_correctness" or not any(
        s.judge_passed is not None for rows in groups for s in rows
    ):
        return groups
    judged = [[s for s in rows if s.judge_passed is not None] for rows in groups]
    return [rows for rows in judged if rows]


def load_manifest(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "golden" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def results_document(
    config: StockroomConfig,
    scored: Sequence[tuple[int, CaseScores]],
    *,
    judge_label: str,
    git_sha: str | None = None,
    manifest: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``eval_results.json`` document from ``(repeat index, scores)`` pairs."""
    scores = [s for _, s in scored]
    manifest = load_manifest(config.data_dir) if manifest is None else manifest
    spread, intervals = uncertainty(scored)
    clash = sorted(set(extra or {}) & RESERVED_KEYS)
    if clash:
        raise ValueError(f"extra may not replace {clash}: the gate checks those fields")
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "mode": config.mode.value,
        "weaknesses": sorted(config.weaknesses),
        "tool_transport": config.tool_transport.value,
        "repeats": len({r for r, _ in scored}),
        "git_sha": git_sha,
        "agent_model_id": config.agent_model_id if config.is_live else MOCK_AGENT_MODEL_ID,
        "judge": judge_label,
        "dataset": {k: manifest.get(k) for k in ("name", "version", "sha256", "cases")},
        "metrics": aggregate(scores),
        "run_to_run": spread,
        "confidence_intervals": intervals,
        **dict(extra or {}),
        "cases": [s.model_dump(mode="json") for s in scores],
    }


def score_suite(
    run_case: Callable[[Any], CaseScores], cases: Sequence[Any], repeats: int = 1
) -> list[tuple[int, CaseScores]]:
    """Score every case ``repeats`` times: ``run_case(case) -> CaseScores``."""
    return [(r, run_case(case)) for r in range(repeats) for case in cases]
