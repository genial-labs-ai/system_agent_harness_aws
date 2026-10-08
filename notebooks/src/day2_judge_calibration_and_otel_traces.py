# %% [markdown]
# # Day 2 — Judge Calibration and OpenTelemetry Traces
#
# **Learning objectives.** By the end of this notebook you can:
#
# 1. Instrument an agent run with OpenTelemetry (GenAI semantic conventions), read the span tree and
#    the `gen_ai.*` attributes, and ship the same spans to Arize Phoenix.
# 2. Detect loops, repeated calls and context growth from a run or from its finished trace with
#    `ToolCallEvaluator`.
# 3. Do error analysis *before* picking metrics: read raw outputs, write down what went wrong, then
#    turn the notes into checks.
# 4. Calibrate an LLM judge against human labels (agreement, Cohen's kappa, confusion matrix) and
#    probe it for position, verbosity and self-preference bias.
# 5. Change a rubric and measure whether the judge got better, not just different.
#
# Three graded exercises; each check cell prints `not solved yet` until your code passes, and the
# *Exercise checklist* cell near the end lists their status (`STOCKROOM_STRICT_EXERCISES=1` makes
# an unsolved exercise an error). Everything runs offline in mock mode with a deterministic fake
# model and fake judge.

# %% [markdown]
# ## Setup

# %%
import importlib.util
import os
import subprocess
from pathlib import Path

# Published repository (docs/DECISIONS.md entry 2). Only used when the package is not already
# installed, e.g. in a fresh Colab or SageMaker Studio kernel.
REPO_URL = "https://github.com/genial-labs-ai/system_agent_harness_aws"
REPO_DIR = Path("stockroom-workshop")

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

if importlib.util.find_spec("stockroom") is None:
    if not REPO_DIR.exists():
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)], check=True)
    # Pins mirror pyproject.toml; everything else (boto3, pydantic, OpenTelemetry, ...) is
    # resolved from the package metadata by the editable install.
    %pip install -q "deepeval==4.2.8" "ragas==0.4.3" "mcp==2.3.0" "jsonschema==4.26.0" "pyyaml==6.0.3" "langchain-community>=0.3.27,<0.4" "pandas>=2.2" "matplotlib>=3.9" -e ./stockroom-workshop
print("stockroom importable:", importlib.util.find_spec("stockroom") is not None)

# %%
from stockroom.config import detect_mode

config = detect_mode()

# %%
import pandas as pd

from stockroom.agent.harness import Harness
from stockroom.config import StockroomConfig, TraceExporter
from stockroom.evals.golden import golden_by_id, load_calibration
from stockroom.evals.judge import FakeJudge, JudgeInput, make_judge
from stockroom.evals.metrics import ToolCallEvaluator, evaluate_case
from stockroom.evals.otel_tracer import RunTracer, configure_tracing, span_tree
from stockroom.exercises import exercise_passed, exercise_pending, exercise_summary

pd.set_option("display.max_colwidth", 90)
pd.set_option("display.width", 160)
by_id = golden_by_id()
judge = make_judge(config)

# %% [markdown]
# ## 1. Error analysis before metrics
#
# Before choosing metrics, look at raw outputs and write down what is actually wrong. This is the
# practice described by Hamel Husain in *Your AI Product Needs Evals*
# (https://hamel.dev/blog/posts/evals/) and studied by Shankar et al. in *Who Validates the
# Validators?* (https://arxiv.org/abs/2404.12272): people only discover what their grading criteria
# should be by reading outputs, and the criteria keep drifting as they read more.
#
# We will do a small version of that. Run eight golden queries with the `ambiguous_tool_desc`
# weakness on, read the trajectories, and *then* decide what to measure.

# %%
sample_ids = ["G001", "G007", "G011", "G014", "G017", "G026", "G033", "G037"]
weak = Harness(config.replace(weaknesses="ambiguous_tool_desc"))
notes = []
for cid in sample_ids:
    case = by_id[cid]
    run = weak.run(case.query, case_id=cid)
    notes.append(
        {
            "case_id": cid,
            "query": case.query,
            "tools": run.tool_names,
            "answer": run.final_answer[:90],
        }
    )
pd.DataFrame(notes)

# %% [markdown]
# Reading the table, the stock and order questions were answered from `search_products` results.
# The answers often *contain* the right number, so an answer-only grader would pass them — yet the
# agent is doing the wrong thing and will break the moment a product has several variants. The
# observation becomes a metric: **tool selection accuracy**, which `evaluate_case` already computes.
# Keep a notes table like this one for every eval you build; the metrics come second.

# %%
scores = pd.DataFrame(
    [
        evaluate_case(by_id[c], weak.run(by_id[c].query, case_id=c), judge).model_dump()
        for c in sample_ids
    ]
)[["case_id", "tool_selection", "answer_correctness_deterministic", "judge_passed"]]
scores

# %% [markdown]
# ## 2. Instrumenting runs with OpenTelemetry
#
# `configure_tracing(TraceExporter.MEMORY)` returns a `TracingHandle` with an in-memory exporter;
# `RunTracer(handle)` is what the harness uses to open one `invoke_agent` span per run, one
# `chat {model}` span per model call and one `execute_tool {tool}` span per tool call. Attribute
# names follow the OpenTelemetry GenAI semantic conventions (status *Development*; the names are
# spelled out in `stockroom/evals/semconv.py` and cross-checked against the installed
# `opentelemetry-semantic-conventions` package by `tests/test_tracer.py`).

# %%
handle = configure_tracing(TraceExporter.MEMORY)
tracer = RunTracer(handle)
traced = Harness(config, tracer=tracer)

case = by_id["G033"]
run = traced.run(case.query, case_id=case.id)
spans = handle.finished_spans()
print(f"{len(spans)} spans, trace_id={run.trace_id}\n")
print(span_tree(spans))

# %% [markdown]
# One model-call span and one tool span, attribute by attribute:

# %%
def gen_ai_attributes(span) -> dict[str, object]:
    return {k: v for k, v in sorted(span.attributes.items()) if k.startswith("gen_ai.")}


chat_span = next(s for s in spans if s.name.startswith("chat "))
tool_span = next(s for s in spans if s.name == "execute_tool get_stock_level")
print("chat span:", chat_span.name)
for k, v in gen_ai_attributes(chat_span).items():
    print(f"  {k} = {v!r}")
print("\ntool span:", tool_span.name)
for k, v in gen_ai_attributes(tool_span).items():
    print(f"  {k} = {v!r}")
print("\nworkshop-specific attributes on the tool span:")
for k, v in sorted(tool_span.attributes.items()):
    if k.startswith("stockroom."):
        print(f"  {k} = {v!r}")

# %% [markdown]
# ### Optional: the same spans in Arize Phoenix
#
# `configure_tracing(TraceExporter.PHOENIX)` swaps the in-memory exporter for OTLP/HTTP. Start a
# local Phoenix with `make phoenix` (needs `uv sync --extra phoenix`) and set
# `PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006`; the cell is skipped otherwise so that the
# notebook never waits on a collector that is not there.

# %%
phoenix_endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
if phoenix_endpoint:
    phoenix_handle = configure_tracing(TraceExporter.PHOENIX, endpoint=phoenix_endpoint)
    Harness(config, tracer=RunTracer(phoenix_handle)).run(case.query, case_id=case.id)
    phoenix_handle.flush()
    print(f"exported one run to Phoenix at {phoenix_endpoint}; open it in the browser")
else:
    installed = importlib.util.find_spec("phoenix") is not None
    print(
        "Phoenix export skipped: PHOENIX_COLLECTOR_ENDPOINT is not set "
        f"(arize-phoenix installed: {installed}; run `make phoenix` and export the variable)."
    )

# %% [markdown]
# ## 3. Loops and context growth with `ToolCallEvaluator`
#
# `ToolCallEvaluator` works from a `RunResult` (`from_run`) or from finished spans (`from_spans`),
# so the same checks can run in CI and over traces collected in production. Two seeded weaknesses:
#
# * `naive_retry`: the legacy order shard is "down" and the model re-issues the identical call;
# * `oversized_payload`: `search_products` dumps the whole catalogue into the context.

# %%
evaluator = ToolCallEvaluator(window=config.repeat_call_window)

handle.clear()
retry_run = Harness(config.replace(weaknesses="naive_retry"), tracer=tracer).run(
    by_id["G043"].query, case_id="G043"
)
retry_report = evaluator.from_run(retry_run)
print(f"termination={retry_run.termination_reason.value} tools={retry_run.tool_names[:4]}...")
print(
    f"loops={retry_report.loops} repeated_identical={retry_report.repeated_identical} "
    f"healthy={retry_report.healthy}"
)
for finding in retry_report.findings:
    print(f"  {finding.kind}: {finding.signature} x{finding.count} at steps {finding.steps}")

# %%
from_trace = evaluator.from_spans(handle.finished_spans(), run_id=retry_run.run_id)
print("same verdict from the OTEL spans:", (from_trace.loops, from_trace.repeated_identical))
assert (from_trace.loops, from_trace.repeated_identical) == (
    retry_report.loops,
    retry_report.repeated_identical,
)

# %%
bloat_case = by_id["G049"]
clean_run = Harness(config).run(bloat_case.query, case_id=bloat_case.id)
bloat_run = Harness(config.replace(weaknesses="oversized_payload")).run(
    bloat_case.query, case_id=bloat_case.id
)
growth = pd.DataFrame(
    [
        {
            "config": label,
            "termination": r.termination_reason.value,
            "context_first": rep.context_tokens_first,
            "context_last": rep.context_tokens_last,
            "growth_ratio": rep.context_growth_ratio,
            "max_context": rep.max_context_tokens,
            "compactions": rep.compactions,
            "input_tokens": r.usage.input_tokens,
        }
        for label, r, rep in (
            ("fixed", clean_run, evaluator.from_run(clean_run)),
            ("oversized_payload", bloat_run, evaluator.from_run(bloat_run)),
        )
    ]
)
growth

# %% [markdown]
# ## 4. Calibrating the judge against human labels
#
# `data/judge_calibration/calibration_v1.jsonl` holds 32 (query, answer, context) triples labelled
# pass/fail by the authors, including paraphrases, hallucinated extras, partial answers and two
# "string-correct but misleading" items. Eight of them are bias probes and are excluded from the
# agreement numbers.
#
# `FakeJudge` has two rubric versions on purpose: **v1** passes any answer containing at least half
# of the key facts (lenient, verbosity-blind), **v2** requires every key fact and no forbidden
# content. `calibrate()` grades every item and compares with the human label.

# %%
from stockroom.evals.calibration import calibrate, run_all_probes
from stockroom.evals.judge import load_rubric

items = load_calibration()
print(f"{len(items)} items, {sum(1 for i in items if i.probe_group)} of them bias probes\n")
print(load_rubric("answer_correctness", "v1"))
print("\n---\n")
print(load_rubric("answer_correctness", "v2"))

# %%
report_v1 = calibrate(FakeJudge("v1"), items)
report_v2 = calibrate(FakeJudge("v2"), items)
print(report_v1.summary())
print()
print(report_v2.summary())


def disagreements(report) -> pd.DataFrame:
    return pd.DataFrame(
        report.disagreements, columns=["item", "human_pass", "judge_pass", "judge_rationale"]
    )


print("\nv1 disagreements:")
display(disagreements(report_v1))
print("v2 disagreements:")
display(disagreements(report_v2))

# %% [markdown]
# Agreement went up and the false-positive count went down, but v2 still disagrees with the humans
# on C23 and C24: answers whose key facts are all present yet which add invented projections. A
# fact-matching rubric cannot see that; this is where a real model judge (live mode) earns its cost.
#
# ### Bias probes
#
# Three probes reuse the same answers under different presentation: swapped order (position),
# padded wording (verbosity) and an "authored by your own model family" label (self-preference). A
# fair judge scores the pairs identically.

# %%
probes = pd.DataFrame(
    [
        {"probe": p.probe, "group": p.group, "detail": p.detail, "biased": p.biased}
        for p in run_all_probes(FakeJudge("v2"), items)
    ]
)
probes

# %% [markdown]
# ### Exercise 1 — rubric v3: catch the two misleading answers
#
# Write a `JudgeV3` that keeps everything v2 does and additionally fails answers that present
# speculation as fact (C23 projects usage "for months"; C24 claims the warranty is "usually"
# extended). Implement it as a subclass of `FakeJudge` that post-processes the v2 verdict.
#
# Success criteria: agreement **and** kappa are higher than v2's on the calibration set, and the
# three bias probes still report no bias.

# %% tags=["exercise"]
from stockroom.evals.judge import JudgeVerdict


class JudgeV3(FakeJudge):
    """Rubric v3 = rubric v2 + a check for speculative language."""

    name = "fake-judge"

    def __init__(self) -> None:
        super().__init__("v2")
        self.rubric_version = "v3"

    def grade(self, item: JudgeInput) -> JudgeVerdict:
        verdict = super().grade(item)
        # TODO: if the answer contains speculative phrasing, return a failing copy of `verdict`
        #       (verdict.model_copy(update={...})) with a rationale that says why.
        return verdict


# %% tags=["solution"]
import re

from stockroom.evals.judge import JudgeVerdict

SPECULATION = re.compile(
    r"(?i)\b(roughly|usually|in practice|should last|for months|on request|probably|typically)\b"
)


class JudgeV3(FakeJudge):
    """Rubric v3 = rubric v2 + a check for speculative language."""

    name = "fake-judge"

    def __init__(self) -> None:
        super().__init__("v2")
        self.rubric_version = "v3"

    def grade(self, item: JudgeInput) -> JudgeVerdict:
        verdict = super().grade(item)
        if item.rubric != "answer_correctness" or not verdict.passed:
            return verdict
        hit = SPECULATION.search(item.answer)
        if hit is None:
            return verdict
        return verdict.model_copy(
            update={
                "passed": False,
                "score": min(verdict.score, 0.6),
                "rationale": verdict.rationale + f"; speculative claim ({hit.group(0)!r})",
                "rubric_version": self.rubric_version,
            }
        )


# %% tags=["check"]
report_v3 = calibrate(JudgeV3(), items)
v3_probes = run_all_probes(JudgeV3(), items)
if report_v3.agreement == report_v2.agreement and report_v3.kappa == report_v2.kappa:
    exercise_pending("day2.ex1", "v3 behaves exactly like v2")
else:
    print(report_v3.summary())
    assert report_v3.agreement > report_v2.agreement, "agreement did not improve over v2"
    assert report_v3.kappa > report_v2.kappa, "kappa did not improve over v2"
    assert not any(p.biased for p in v3_probes), "v3 introduced a bias"
    exercise_passed(
        "day2.ex1",
        f"agreement {report_v2.agreement:.2%} -> {report_v3.agreement:.2%}, "
        f"kappa {report_v2.kappa:.3f} -> {report_v3.kappa:.3f}, disagreements "
        f"{[d[0] for d in report_v3.disagreements]}",
    )

# %% [markdown]
# ### Exercise 2 — add a disagreement to the calibration set
#
# A calibration set is only useful if it contains items the judge gets wrong. Write a new
# `CalibrationItem` (a query, an answer, the context the answer was produced from, the human label)
# on which `FakeJudge("v2")` **disagrees** with your human label. Hint: v2 only checks facts and
# forbidden strings.
#
# Success criteria: the item validates, its id is new, and the v2 verdict differs from `human_label`.

# %%
from stockroom.evals.calibration import to_judge_input
from stockroom.evals.golden import CalibrationItem

print(items[22].model_dump_json(indent=2))  # C23, for the shape

# %% tags=["exercise"]
def new_calibration_item() -> CalibrationItem | None:
    """A labelled item on which rubric v2 disagrees with the human (None = not solved yet)."""
    # TODO: return CalibrationItem(id="C33", query=..., answer=..., context=..., expected_facts=[...],
    #       forbidden_facts=[...], human_label="pass" | "fail", human_score=1..5, rationale=...)
    return None


# %% tags=["solution"]
def new_calibration_item() -> CalibrationItem | None:
    """A labelled item on which rubric v2 disagrees with the human (None = not solved yet)."""
    return CalibrationItem(
        id="C33",
        query="How many SKU-1015 are in stock?",
        answer=(
            "SKU-1015 has 42 units in stock, so you can safely skip the next three stock counts "
            "and cancel the standing supplier order."
        ),
        context=(
            "Tool result get_stock_level: SKU-1015 Nitrile Gloves Large (box of 100), "
            "stock_level 42, reorder_point 10, aisle 3 bin 2."
        ),
        expected_facts=["42"],
        forbidden_facts=["0 units"],
        human_label="fail",
        human_score=2,
        rationale="Correct number, but recommends operational actions the context does not support.",
    )


# %% tags=["check"]
new_item = new_calibration_item()
if new_item is None:
    exercise_pending("day2.ex2")
else:
    assert isinstance(new_item, CalibrationItem)
    assert new_item.id not in {i.id for i in items}, "pick an unused id"
    v2_verdict = FakeJudge("v2").grade(to_judge_input(new_item))
    assert v2_verdict.passed != new_item.human_pass, (
        f"v2 agrees with you (judge pass={v2_verdict.passed}); make the item harder"
    )
    exercise_passed(
        "day2.ex2",
        f"human={new_item.human_label} judge v2 pass={v2_verdict.passed} ({v2_verdict.rationale})",
    )

# %% [markdown]
# ### Exercise 3 — find the loop in a trace from its spans
#
# `ToolCallEvaluator.from_spans` did this for you in section 3. Now do it by hand from the
# `execute_tool` spans of the `naive_retry` run (still in `handle`): group tool spans by
# (`gen_ai.tool.name`, `gen_ai.tool.call.arguments`) and return the most repeated signature.
#
# Success criteria: `most_repeated_tool_call(spans)` returns `(tool_name, count)` matching the
# `loop` finding that `ToolCallEvaluator` reported for the same run.

# %%
from stockroom.evals import semconv as sc

retry_spans = [
    s for s in handle.finished_spans() if s.attributes.get(sc.GEN_AI_OPERATION_NAME) == "execute_tool"
]
print(f"{len(retry_spans)} tool spans; attribute keys to use: {sc.GEN_AI_TOOL_NAME!r}, "
      f"{sc.GEN_AI_TOOL_CALL_ARGUMENTS!r}")

# %% tags=["exercise"]
def most_repeated_tool_call(tool_spans) -> tuple[str, int] | None:
    """(tool name, number of identical calls) for the most repeated call (None = not solved)."""
    # TODO: count (gen_ai.tool.name, gen_ai.tool.call.arguments) pairs and return the top one.
    return None


# %% tags=["solution"]
from collections import Counter


def most_repeated_tool_call(tool_spans) -> tuple[str, int] | None:
    """(tool name, number of identical calls) for the most repeated call (None = not solved)."""
    counts: Counter[tuple[str, str]] = Counter(
        (
            str(s.attributes.get(sc.GEN_AI_TOOL_NAME)),
            str(s.attributes.get(sc.GEN_AI_TOOL_CALL_ARGUMENTS)),
        )
        for s in tool_spans
    )
    if not counts:
        return None
    (name, _args), count = counts.most_common(1)[0]
    return name, count


# %% tags=["check"]
answer = most_repeated_tool_call(retry_spans)
if answer is None:
    exercise_pending("day2.ex3")
else:
    loop = next(f for f in retry_report.findings if f.kind == "loop")
    expected = (loop.signature.split(":", 1)[0], loop.count)
    assert answer == expected, f"got {answer}, ToolCallEvaluator says {expected}"
    exercise_passed("day2.ex3", f"{answer[0]} was called {answer[1]} times with identical arguments")

# %% [markdown]
# ## Save your work for Day 4
#
# The Day 4 gate's `answer_correctness` is graded by a judge, so the capstone review should say how
# far that judge can be trusted. This cell saves what you measured today: agreement and kappa for
# rubric v2 and for your rubric (Exercise 1), and your calibration item (Exercise 2), which Day 4
# hands to the gate's judge. It writes `reports/participant/day2.json` (or into
# `STOCKROOM_HANDOFF_DIR`) once both exercises have passed; otherwise Day 4 uses the reference
# artefact from `data/handoff/` and says so.

# %%
from stockroom.exercises import PASSED, exercise_status
from stockroom.handoff import CalibrationSummary, Day2Handoff, save_handoff

if all(exercise_status(e) == PASSED for e in ("day2.ex1", "day2.ex2")):
    own_calibration = Day2Handoff(
        mode=config.mode,
        calibrations=[CalibrationSummary.from_report(r) for r in (report_v2, report_v3)],
        calibration_item=new_calibration_item(),
    )
    saved = save_handoff(own_calibration)
    print(f"saved rubrics {[c.rubric_version for c in own_calibration.calibrations]} and item "
          f"{own_calibration.calibration_item.id} to {saved}")
else:
    print("Nothing saved: Exercises 1 and 2 have not both passed, so Day 4 will use the reference "
          "calibration.")

# %% [markdown]
# ## Exercise checklist
#
# One line per graded exercise. With `STOCKROOM_STRICT_EXERCISES=1` this cell fails unless every
# exercise passed; `make notebooks` runs the solution notebooks that way.

# %%
exercise_summary(["day2.ex1", "day2.ex2", "day2.ex3"])

# %% [markdown]
# ## Wrap-up
#
# * Read outputs first, write notes, then turn the notes into metrics (Hamel Husain's and
#   Shankar et al.'s advice); `tool_selection` came out of exactly that loop today.
# * One `invoke_agent` span per run with `chat` and `execute_tool` children is enough to recompute
#   loop and context-growth metrics after the fact, in Phoenix or in CloudWatch.
# * A judge is a model with its own failure modes: measure agreement and kappa against human
#   labels, keep disagreements in the calibration set, and re-run the bias probes after every
#   rubric change.
#
# Day 3 opens the harness itself: the state machine, guards, compaction and the MCP tool boundary.
