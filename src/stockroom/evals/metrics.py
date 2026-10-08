"""Deterministic and judge-based metrics over a :class:`RunResult` and its golden case.

Deterministic: tool-selection accuracy, argument correctness, trajectory match (exact /
in-order subset / any-order), step count, termination reason, must-not-call, expected and
forbidden facts. :class:`ToolCallEvaluator` inspects a run (or a finished OTEL trace) for repeated
identical calls, loops, redundant queries, invalid calls and context growth.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from pydantic import BaseModel, Field

from stockroom.agent.retrieval import content_tokens
from stockroom.agent.types import RunResult, ToolCallRecord
from stockroom.evals.golden import ExpectedTool, GoldenCase
from stockroom.evals.judge import Judge, JudgeInput, JudgeVerdict, fact_present
from stockroom.evals.otel_tracer import LoopFinding, TraceSummary, detect_loops

# --- argument comparison -------------------------------------------------------------------------


def args_match(expected: dict[str, Any], actual: Any) -> bool:
    """Expected args are the *minimal* correct set; free-text ``query`` args match by tokens."""
    if not isinstance(actual, dict):
        return False
    for key, want in expected.items():
        if key not in actual:
            return False
        got = actual[key]
        if key == "query":
            want_tokens = set(content_tokens(str(want)))
            got_tokens = set(content_tokens(str(got)))
            if not want_tokens <= got_tokens:
                return False
        elif isinstance(want, int | float) and not isinstance(want, bool):
            try:
                if float(got) != float(want):
                    return False
            except (TypeError, ValueError):
                return False
        elif str(got).lower() != str(want).lower():
            return False
    return True


def match_calls(
    expected: Sequence[ExpectedTool], actual: Sequence[ToolCallRecord], mode: str
) -> tuple[bool, list[ToolCallRecord | None]]:
    """Return (names_match, aligned actual record per expected call or None)."""
    names_actual = [r.name for r in actual]
    names_expected = [e.name for e in expected]
    aligned: list[ToolCallRecord | None] = []
    if mode == "exact":
        ok = names_actual == names_expected
        aligned = list(actual) if ok else [None] * len(expected)
    elif mode == "in_order_subset":
        pos = 0
        for exp in expected:
            found = None
            while pos < len(actual):
                rec = actual[pos]
                pos += 1
                if rec.name == exp.name:
                    found = rec
                    break
            aligned.append(found)
        ok = all(a is not None for a in aligned)
    else:  # any_order
        ok = Counter(names_actual) == Counter(names_expected)
        pool = list(actual)
        for exp in expected:
            best = None
            for rec in pool:
                if rec.name == exp.name and (best is None or args_match(exp.args, rec.arguments)):
                    best = rec
                    if args_match(exp.args, rec.arguments):
                        break
            if best is not None:
                pool.remove(best)
            aligned.append(best)
    return ok, aligned


def tool_selection_score(case: GoldenCase, run: RunResult) -> float:
    executed = run.executed_tool_calls
    if any(r.name in case.must_not_call for r in executed):
        return 0.0
    ok, _ = match_calls(case.expected_tools, executed, case.trajectory_match_mode)
    return 1.0 if ok else 0.0


def argument_correctness_score(case: GoldenCase, run: RunResult) -> float:
    if not case.expected_tools:
        return 1.0 if not run.executed_tool_calls else 0.0
    _, aligned = match_calls(
        case.expected_tools, run.executed_tool_calls, case.trajectory_match_mode
    )
    hits = sum(
        1
        for exp, rec in zip(case.expected_tools, aligned, strict=True)
        if rec is not None and args_match(exp.args, rec.arguments)
    )
    return hits / len(case.expected_tools)


def trajectory_matches(case: GoldenCase, run: RunResult) -> bool:
    return tool_selection_score(case, run) == 1.0 and argument_correctness_score(case, run) == 1.0


# --- output checks --------------------------------------------------------------------------------


class OutputCheck(BaseModel):
    facts_found: list[str] = Field(default_factory=list)
    facts_missing: list[str] = Field(default_factory=list)
    forbidden_found: list[str] = Field(default_factory=list)
    must_not_call_violations: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.facts_missing
            and not self.forbidden_found
            and not self.must_not_call_violations
        )


class OutputEvaluator:
    """Deterministic answer checks against the golden case."""

    @staticmethod
    def evaluate(case: GoldenCase, run: RunResult) -> OutputCheck:
        answer = run.final_answer
        found = [f for f in case.expected_facts if fact_present(f, answer)]
        missing = [f for f in case.expected_facts if f not in found]
        forbidden = [f for f in case.forbidden_facts if fact_present(f, answer)]
        violations = sorted(
            {r.name for r in run.executed_tool_calls if r.name in case.must_not_call}
        )
        return OutputCheck(
            facts_found=found,
            facts_missing=missing,
            forbidden_found=forbidden,
            must_not_call_violations=violations,
        )


# --- trajectory health ----------------------------------------------------------------------------


@dataclass
class ToolCallReport:
    total_calls: int
    executed_calls: int
    invalid_calls: int
    error_calls: int
    repeated_identical: int
    loops: int
    redundant_queries: int
    context_tokens_first: int
    context_tokens_last: int
    context_growth_ratio: float
    max_context_tokens: int
    compactions: int
    blocked_calls: int = 0
    findings: list[LoopFinding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.loops == 0 and self.repeated_identical == 0 and self.invalid_calls == 0


class ToolCallEvaluator:
    """Inspect a run's trajectory (or an OTEL trace) for loop-like behaviour and context growth."""

    def __init__(self, window: int = 3) -> None:
        self.window = window

    def from_run(self, run: RunResult) -> ToolCallReport:
        summary = TraceSummary(
            run_id=run.run_id,
            case_id=run.case_id,
            termination_reason=run.termination_reason.value,
            compactions=len(run.compaction_events),
        )
        from stockroom.evals.otel_tracer import TraceModelCall, TraceToolCall

        for rec in run.tool_records:
            summary.tool_calls.append(
                TraceToolCall(
                    name=rec.name,
                    arguments=json.dumps(rec.arguments, sort_keys=True, default=str),
                    step=rec.step,
                    is_error=rec.is_error,
                    latency_ms=rec.latency_ms,
                    span_id=rec.tool_use_id,
                )
            )
        for turn in run.model_turns:
            summary.model_calls.append(
                TraceModelCall(
                    step=turn.step,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                    context_tokens=turn.context_tokens_estimate,
                    latency_ms=turn.latency_ms,
                )
            )
        return self._report(
            summary, invalid=len(run.invalid_tool_calls), blocked=len(run.blocked_tool_calls)
        )

    def from_spans(
        self, spans: Sequence[ReadableSpan], run_id: str | None = None
    ) -> ToolCallReport:
        summary = TraceSummary.from_spans(spans, run_id=run_id)
        return self._report(summary, invalid=0)

    def _report(self, summary: TraceSummary, invalid: int, blocked: int = 0) -> ToolCallReport:
        findings = detect_loops(summary, window=self.window)
        contexts = [m.context_tokens or m.input_tokens for m in summary.model_calls]
        first = contexts[0] if contexts else 0
        last = contexts[-1] if contexts else 0
        return ToolCallReport(
            total_calls=len(summary.tool_calls),
            executed_calls=len(summary.tool_calls) - invalid - blocked,
            invalid_calls=invalid,
            blocked_calls=blocked,
            error_calls=sum(1 for c in summary.tool_calls if c.is_error),
            repeated_identical=sum(1 for f in findings if f.kind == "repeated_identical_call"),
            loops=sum(1 for f in findings if f.kind == "loop"),
            redundant_queries=sum(1 for f in findings if f.kind == "redundant_query"),
            context_tokens_first=first,
            context_tokens_last=last,
            context_growth_ratio=round(last / first, 3) if first else 1.0,
            max_context_tokens=max(contexts) if contexts else 0,
            compactions=summary.compactions,
            findings=findings,
        )


# --- per-case scoring -----------------------------------------------------------------------------


class CaseScores(BaseModel):
    case_id: str
    category: str
    tool_selection: float
    argument_correctness: float
    trajectory_match: bool
    termination_match: bool
    must_not_call_ok: bool
    step_count_ok: bool
    steps: int
    model_calls: int
    answer_correctness_deterministic: bool
    facts_missing: list[str] = Field(default_factory=list)
    forbidden_found: list[str] = Field(default_factory=list)
    answer_correctness_judge: float | None = None
    judge_passed: bool | None = None
    judge_rationale: str | None = None
    faithfulness_judge: float | None = None
    loop_findings: int = 0
    repeated_identical: int = 0
    invalid_calls: int = 0
    blocked_calls: int = 0
    compactions: int = 0
    context_growth_ratio: float = 1.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    termination_reason: str
    tool_names: list[str] = Field(default_factory=list)
    final_answer: str
    run_id: str
    trace_id: str | None = None


def context_from_run(run: RunResult) -> str:
    parts = []
    for rec in run.executed_tool_calls:
        args = json.dumps(rec.arguments, default=str)
        body = json.dumps(rec.result_content, default=str)[:1500]
        parts.append(f"{rec.name}({args}) -> {body}")
    return "\n".join(parts)


def evaluate_case(
    case: GoldenCase, run: RunResult, judge: Judge | None = None, window: int = 3
) -> CaseScores:
    output = OutputEvaluator.evaluate(case, run)
    report = ToolCallEvaluator(window=window).from_run(run)
    verdict: JudgeVerdict | None = None
    faithfulness: JudgeVerdict | None = None
    if judge is not None:
        verdict = judge.grade(
            JudgeInput(
                query=case.query,
                answer=run.final_answer,
                reference=case.reference_answer,
                context=context_from_run(run),
                expected_facts=case.expected_facts,
                forbidden_facts=case.forbidden_facts,
                rubric="answer_correctness",
            )
        )
        if run.executed_tool_calls:
            faithfulness = judge.grade(
                JudgeInput(
                    query=case.query,
                    answer=run.final_answer,
                    reference=case.reference_answer,
                    context=context_from_run(run),
                    rubric="faithfulness",
                )
            )
    return CaseScores(
        case_id=case.id,
        category=case.category,
        tool_selection=tool_selection_score(case, run),
        argument_correctness=argument_correctness_score(case, run),
        trajectory_match=trajectory_matches(case, run),
        termination_match=run.termination_reason.value == case.expected_termination,
        must_not_call_ok=not output.must_not_call_violations,
        step_count_ok=case.max_steps is None or run.usage.model_calls <= case.max_steps,
        steps=run.steps,
        model_calls=run.usage.model_calls,
        answer_correctness_deterministic=output.passed,
        facts_missing=output.facts_missing,
        forbidden_found=output.forbidden_found,
        answer_correctness_judge=verdict.score if verdict else None,
        judge_passed=verdict.passed if verdict else None,
        judge_rationale=verdict.rationale if verdict else None,
        faithfulness_judge=faithfulness.score if faithfulness else None,
        loop_findings=report.loops,
        repeated_identical=report.repeated_identical,
        invalid_calls=report.invalid_calls,
        blocked_calls=report.blocked_calls,
        compactions=report.compactions,
        context_growth_ratio=report.context_growth_ratio,
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        cost_usd=run.cost_estimate.usd,
        termination_reason=run.termination_reason.value,
        tool_names=run.tool_names,
        final_answer=run.final_answer,
        run_id=run.run_id,
        trace_id=run.trace_id,
    )


def aggregate(scores: Sequence[CaseScores]) -> dict[str, Any]:
    """Aggregate metrics in the shape ``scripts/check_thresholds.py`` consumes."""
    n = len(scores)
    if n == 0:
        return {"cases": 0}

    def mean(values: Sequence[float]) -> float:
        return round(statistics.fmean(values), 4) if values else 0.0

    judged = [s for s in scores if s.judge_passed is not None]
    by_category: dict[str, dict[str, Any]] = {}
    for cat in sorted({s.category for s in scores}):
        rows = [s for s in scores if s.category == cat]
        by_category[cat] = {
            "cases": len(rows),
            "tool_selection_accuracy": mean([s.tool_selection for s in rows]),
            "answer_correctness": mean(
                [float(s.judge_passed) for s in rows if s.judge_passed is not None]
            )
            if any(s.judge_passed is not None for s in rows)
            else mean([float(s.answer_correctness_deterministic) for s in rows]),
            "termination_match_rate": mean([float(s.termination_match) for s in rows]),
        }
    return {
        "cases": n,
        "tool_selection_accuracy": mean([s.tool_selection for s in scores]),
        "argument_correctness": mean([s.argument_correctness for s in scores]),
        "trajectory_match_rate": mean([float(s.trajectory_match) for s in scores]),
        "answer_correctness": mean([float(s.judge_passed) for s in judged])
        if judged
        else mean([float(s.answer_correctness_deterministic) for s in scores]),
        "answer_correctness_deterministic": mean(
            [float(s.answer_correctness_deterministic) for s in scores]
        ),
        "answer_correctness_judge_mean_score": mean(
            [s.answer_correctness_judge for s in judged if s.answer_correctness_judge is not None]
        ),
        "faithfulness_mean_score": mean(
            [s.faithfulness_judge for s in scores if s.faithfulness_judge is not None]
        ),
        "termination_match_rate": mean([float(s.termination_match) for s in scores]),
        "must_not_call_ok_rate": mean([float(s.must_not_call_ok) for s in scores]),
        "step_count_ok_rate": mean([float(s.step_count_ok) for s in scores]),
        "loop_rate": mean([float(s.loop_findings > 0 or s.repeated_identical > 0) for s in scores]),
        "invalid_call_rate": mean([float(s.invalid_calls > 0) for s in scores]),
        "blocked_call_rate": mean([float(s.blocked_calls > 0) for s in scores]),
        "compaction_rate": mean([float(s.compactions > 0) for s in scores]),
        "mean_model_calls": mean([s.model_calls for s in scores]),
        "mean_input_tokens": mean([s.input_tokens for s in scores]),
        "mean_output_tokens": mean([s.output_tokens for s in scores]),
        "total_cost_usd": round(sum(s.cost_usd or 0.0 for s in scores), 6),
        "cost_known": all(s.cost_usd is not None for s in scores),
        "by_category": by_category,
    }
