"""The statistics behind the gate's thresholds: three separate questions, three separate numbers.

1. **Minimum acceptable quality** (``gates.min`` in ``eval_thresholds.yaml``) is a product decision.
   The data only tells you whether ``main`` clears it reliably: :func:`floor_is_safe`.
2. **Run-to-run variation**: rerun the *same* suite on the *same* code and the suite-level metric
   moves by the model's and judge's sampling noise. :func:`run_to_run` keeps the repeats apart and
   reports their spread; a pull request's single run differs from the baseline's single run by noise
   alone with standard deviation ``sd·√2``.
3. **Allowed regression** (``regression.max_drop_vs_baseline``) must sit above that noise or the
   gate flaps: :func:`required_max_drop`.

Separately, :func:`case_bootstrap_interval` answers "how precisely do these N cases estimate the
agent's rate on this kind of traffic?" by resampling cases (not case×repeat rows, which are not
independent). It is bounded to [0, 1] for rates and deterministic (fixed seed).

In mock mode every repeat is identical, so the spread is exactly zero: that demonstrates
reproducibility, not production reliability. The numbers worth reading come from live runs.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence
from typing import Any

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0
NOISE_SDS = 2.0  # one-sided margin: noise alone exceeds it in roughly 2% of comparisons


def run_to_run(per_repeat: Sequence[float]) -> dict[str, Any]:
    """Spread of one suite-level metric across repeated runs of the same suite on the same code."""
    values = [float(v) for v in per_repeat]
    if not values:
        raise ValueError("run_to_run() needs at least one repeat")
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "per_repeat": [round(v, 4) for v in values],
        "mean": round(statistics.fmean(values), 4),
        "sd": round(sd, 4),
        "repeats": len(values),
    }


def case_bootstrap_interval(
    per_case: Sequence[float],
    *,
    level: float = 0.95,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Percentile bootstrap of the suite mean over cases (each case averaged over its repeats)."""
    values = [float(v) for v in per_case]
    n = len(values)
    if n == 0:
        raise ValueError("case_bootstrap_interval() needs at least one case")
    mean = statistics.fmean(values)
    if n == 1 or min(values) == max(values):
        low = high = mean
    else:
        rng = random.Random(seed)
        means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(resamples))
        tail = (1.0 - level) / 2.0
        low = means[math.floor(tail * (resamples - 1))]
        high = means[math.ceil((1.0 - tail) * (resamples - 1))]
    return {
        "mean": round(mean, 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
        "n": n,
        "method": f"case bootstrap, percentile, {resamples} resamples, seed {seed}",
    }


def round_up(value: float, decimals: int = 2) -> float:
    """Round ``value`` up (towards +inf) to ``decimals`` places, ignoring float dust."""
    scale = 10**decimals
    return math.ceil(round(value * scale, 6)) / scale


def required_max_drop(run_sd: float, sds: float = NOISE_SDS) -> float:
    """Smallest ``max_drop_vs_baseline`` that run-to-run noise alone rarely exceeds.

    A PR run and the baseline run are independent, so their difference has standard deviation
    ``run_sd·√2``; the margin is ``sds`` of those, rounded up to two decimals.
    """
    return round_up(sds * math.sqrt(2.0) * max(run_sd, 0.0))


def floor_is_safe(mean: float, run_sd: float, floor: float, sds: float = NOISE_SDS) -> bool:
    """True when ``main`` clears the product floor even on a run ``sds`` deviations below par."""
    return mean - sds * max(run_sd, 0.0) >= floor
