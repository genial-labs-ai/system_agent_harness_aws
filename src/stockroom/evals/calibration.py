"""Judge calibration against human labels: agreement, Cohen's kappa, confusion, bias probes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from stockroom.evals.golden import CalibrationItem
from stockroom.evals.judge import Judge, JudgeInput, JudgeVerdict


@dataclass
class ConfusionMatrix:
    tp: int = 0  # human pass, judge pass
    fp: int = 0  # human fail, judge pass
    fn: int = 0  # human pass, judge fail
    tn: int = 0  # human fail, judge fail

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    def as_table(self) -> str:
        return (
            "                judge pass  judge fail\n"
            f"human pass      {self.tp:>10}  {self.fn:>10}\n"
            f"human fail      {self.fp:>10}  {self.tn:>10}"
        )


def agreement_rate(human: Sequence[bool], judge: Sequence[bool]) -> float:
    if not human:
        return 0.0
    return sum(1 for h, j in zip(human, judge, strict=True) if h == j) / len(human)


def cohens_kappa(human: Sequence[bool], judge: Sequence[bool]) -> float:
    """Cohen's kappa for two binary raters (1 = perfect agreement, 0 = chance)."""
    n = len(human)
    if n == 0:
        return 0.0
    po = agreement_rate(human, judge)
    p_h = sum(human) / n
    p_j = sum(judge) / n
    pe = p_h * p_j + (1 - p_h) * (1 - p_j)
    if pe == 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def confusion_matrix(human: Sequence[bool], judge: Sequence[bool]) -> ConfusionMatrix:
    cm = ConfusionMatrix()
    for h, j in zip(human, judge, strict=True):
        if h and j:
            cm.tp += 1
        elif h and not j:
            cm.fn += 1
        elif not h and j:
            cm.fp += 1
        else:
            cm.tn += 1
    return cm


@dataclass
class CalibrationReport:
    judge_name: str
    rubric_version: str
    n: int
    agreement: float
    kappa: float
    confusion: ConfusionMatrix
    disagreements: list[tuple[str, bool, bool, str]] = field(default_factory=list)
    verdicts: dict[str, JudgeVerdict] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.judge_name} rubric {self.rubric_version}: n={self.n} "
            f"agreement={self.agreement:.2%} kappa={self.kappa:.3f}\n{self.confusion.as_table()}"
        )


def to_judge_input(item: CalibrationItem) -> JudgeInput:
    return JudgeInput(
        query=item.query,
        answer=item.answer,
        reference=None,
        context=item.context,
        expected_facts=item.expected_facts,
        forbidden_facts=item.forbidden_facts,
        rubric="answer_correctness",
    )


def calibrate(
    judge: Judge, items: Sequence[CalibrationItem], include_probes: bool = False
) -> CalibrationReport:
    """Grade every (non-probe) item and compare with the human label."""
    rows = [i for i in items if include_probes or i.probe_group is None]
    human: list[bool] = []
    machine: list[bool] = []
    disagreements = []
    verdicts: dict[str, JudgeVerdict] = {}
    for item in rows:
        v = judge.grade(to_judge_input(item))
        verdicts[item.id] = v
        human.append(item.human_pass)
        machine.append(v.passed)
        if v.passed != item.human_pass:
            disagreements.append((item.id, item.human_pass, v.passed, v.rationale))
    return CalibrationReport(
        judge_name=judge.name,
        rubric_version=judge.rubric_version,
        n=len(rows),
        agreement=round(agreement_rate(human, machine), 4),
        kappa=cohens_kappa(human, machine),
        confusion=confusion_matrix(human, machine),
        disagreements=disagreements,
        verdicts=verdicts,
    )


@dataclass
class ProbeResult:
    probe: str
    group: str
    detail: str
    biased: bool


def _group(items: Sequence[CalibrationItem], prefix: str) -> dict[str, list[CalibrationItem]]:
    groups: dict[str, list[CalibrationItem]] = {}
    for item in items:
        if item.probe_group and item.probe_group.startswith(prefix):
            groups.setdefault(item.probe_group, []).append(item)
    return groups


def position_bias_probe(judge: Judge, items: Sequence[CalibrationItem]) -> list[ProbeResult]:
    """Pairs ``position:P`` and ``position:P-swapped`` contain the same two answers in opposite
    order. A position-biased judge prefers whichever answer is shown first."""
    results = []
    groups = _group(items, "position:")
    bases = {g for g in groups if not g.endswith("-swapped")}
    for base in sorted(bases):
        pair = groups.get(base, [])
        swapped = groups.get(base + "-swapped", [])
        if len(pair) != 2 or len(swapped) != 2:
            continue
        scores_a = [judge.grade(to_judge_input(i)).score for i in pair]
        scores_b = [judge.grade(to_judge_input(i)).score for i in swapped]
        # The preferred answer is role A_first in `pair` and A_second in `swapped`.
        a_first = next(i for i in pair if i.probe_role == "A_first")
        a_second = next(i for i in swapped if i.probe_role == "A_second")
        sa1 = scores_a[pair.index(a_first)]
        sa2 = scores_b[swapped.index(a_second)]
        biased = abs(sa1 - sa2) > 0.1
        results.append(
            ProbeResult(
                "position",
                base,
                f"preferred answer scored {sa1:.2f} when first and {sa2:.2f} when second",
                biased,
            )
        )
    return results


def verbosity_bias_probe(judge: Judge, items: Sequence[CalibrationItem]) -> list[ProbeResult]:
    results = []
    for group, pair in sorted(_group(items, "verbosity:").items()):
        short = next((i for i in pair if i.probe_role == "short"), None)
        verbose = next((i for i in pair if i.probe_role == "verbose"), None)
        if short is None or verbose is None:
            continue
        s_short = judge.grade(to_judge_input(short)).score
        s_verbose = judge.grade(to_judge_input(verbose)).score
        results.append(
            ProbeResult(
                "verbosity",
                group,
                f"short={s_short:.2f} verbose={s_verbose:.2f} (same facts)",
                s_verbose > s_short + 0.05,
            )
        )
    return results


def self_preference_probe(judge: Judge, items: Sequence[CalibrationItem]) -> list[ProbeResult]:
    """Identical answers labelled as coming from the judge's own family vs another. The label is
    passed in the query prefix so a real judge sees it; a fair judge scores both the same."""
    results = []
    for group, pair in sorted(_group(items, "self_pref:").items()):
        scores = {}
        for item in pair:
            inp = to_judge_input(item)
            family = (
                "the same model family as you"
                if item.probe_role == "same_family"
                else "a different model family"
            )
            inp.query = f"[Answer produced by {family}] {inp.query}"
            scores[item.probe_role] = judge.grade(inp).score
        same, other = scores.get("same_family", 0.0), scores.get("other_family", 0.0)
        results.append(
            ProbeResult(
                "self_preference",
                group,
                f"same_family={same:.2f} other_family={other:.2f}",
                same > other + 0.05,
            )
        )
    return results


def run_all_probes(judge: Judge, items: Sequence[CalibrationItem]) -> list[ProbeResult]:
    return (
        position_bias_probe(judge, items)
        + verbosity_bias_probe(judge, items)
        + self_preference_probe(judge, items)
    )
