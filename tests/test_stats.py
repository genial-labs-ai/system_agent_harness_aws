"""The statistics behind the gate's thresholds (stockroom.evals.stats and evals.report)."""

from __future__ import annotations

import pytest

from stockroom.evals.metrics import CaseScores, aggregate
from stockroom.evals.report import uncertainty
from stockroom.evals.stats import (
    case_bootstrap_interval,
    floor_is_safe,
    required_max_drop,
    round_up,
    run_to_run,
)


def test_run_to_run_keeps_repeats_apart() -> None:
    spread = run_to_run([0.90, 0.88, 0.92])
    assert spread["per_repeat"] == [0.9, 0.88, 0.92]
    assert spread["mean"] == 0.9 and spread["repeats"] == 3
    assert spread["sd"] == pytest.approx(0.02)  # sample sd (n - 1)
    assert run_to_run([1.0])["sd"] == 0.0
    with pytest.raises(ValueError):
        run_to_run([])


def test_case_bootstrap_is_bounded_and_deterministic() -> None:
    rates = [1.0] * 45 + [0.0] * 5
    first = case_bootstrap_interval(rates)
    assert first == case_bootstrap_interval(rates)
    assert 0.0 <= first["ci95_low"] < first["mean"] == 0.9 < first["ci95_high"] <= 1.0
    perfect = case_bootstrap_interval([1.0] * 50)
    assert perfect["ci95_low"] == perfect["ci95_high"] == 1.0


def test_required_max_drop_and_floor_check() -> None:
    assert required_max_drop(0.0) == 0.0
    assert required_max_drop(0.02) == 0.06  # 2 * sqrt(2) * 0.02 = 0.0566 -> rounded up
    assert round_up(0.0701) == 0.08 and round_up(0.07000000001) == 0.07  # float dust ignored
    assert floor_is_safe(mean=0.9, run_sd=0.02, floor=0.85)
    assert not floor_is_safe(mean=0.9, run_sd=0.03, floor=0.85)


def _score(case_id: str, repeat_ok: bool) -> CaseScores:
    return CaseScores(
        case_id=case_id,
        category="stock_lookup",
        tool_selection=1.0,
        argument_correctness=1.0,
        trajectory_match=True,
        termination_match=True,
        must_not_call_ok=True,
        step_count_ok=True,
        steps=2,
        model_calls=2,
        answer_correctness_deterministic=repeat_ok,
        judge_passed=repeat_ok,
        termination_reason="COMPLETED",
        final_answer="",
        run_id="r",
    )


def test_uncertainty_separates_run_noise_from_case_sampling() -> None:
    # Two repeats over three cases; only G003 flips between repeats.
    scored = [
        (0, _score("G001", True)),
        (0, _score("G002", True)),
        (0, _score("G003", True)),
        (1, _score("G001", True)),
        (1, _score("G002", True)),
        (1, _score("G003", False)),
    ]
    spread, intervals = uncertainty(scored)
    assert spread["answer_correctness"]["per_repeat"] == [1.0, 0.6667]
    assert spread["answer_correctness"]["sd"] > 0
    assert spread["tool_selection_accuracy"]["sd"] == 0.0
    # G003 counts once, as 0.5: three cases, not six independent rows.
    assert intervals["answer_correctness"]["n"] == 3
    assert intervals["answer_correctness"]["mean"] == pytest.approx(0.8333, abs=1e-4)


def test_answer_interval_counts_the_cases_aggregate_counts() -> None:
    # Once any case is judged, aggregate() ignores unjudged ones; the interval must too.
    unjudged = _score("G002", False).model_copy(update={"judge_passed": None})
    scored = [(0, _score("G001", True)), (0, unjudged)]
    _, intervals = uncertainty(scored)
    assert aggregate([s for _, s in scored])["answer_correctness"] == 1.0
    assert intervals["answer_correctness"]["mean"] == 1.0
    assert intervals["answer_correctness"]["n"] == 1
