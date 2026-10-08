# %% [markdown]
# # Day 3 — Building a Custom Agent Harness
#
# *Agent = Model + Harness.* Most production failures live in the harness and the environment, not
# in the model. Today we open the Stockroom harness (`src/stockroom/agent/harness.py`) seam by seam.
#
# **Learning objectives.** By the end of this notebook you can:
#
# 1. Read the harness as an explicit state machine (`PLAN → CALL_MODEL → EXECUTE_TOOLS → OBSERVE →
#    DONE | FAILED`) and use its transition log to explain a run.
# 2. Show how schema validation intercepts a malformed tool call before it reaches the tool.
# 3. Trigger each guard (max steps, token budget, repeated call, wall clock) and read its
#    `GuardEvent`.
# 4. Trigger context compaction and tool-output quarantine, and see them in the trace.
# 5. Run the same harness through an MCP tool server instead of in-process tools.
# 6. Measure what each seeded weakness costs, fix the weaknesses one at a time, and chart the
#    metric movement.
#
# Three graded exercises; each check cell prints `not solved yet` until your code passes, and the
# *Exercise checklist* cell near the end lists their status (`STOCKROOM_STRICT_EXERCISES=1` makes
# an unsolved exercise an error). Everything runs offline in mock mode.

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
from typing import Any

import pandas as pd

from stockroom.agent.harness import Harness
from stockroom.agent.types import ModelResponse, TerminationReason, ToolCallRequest
from stockroom.config import StockroomConfig, TraceExporter
from stockroom.evals.golden import golden_by_id, load_golden
from stockroom.evals.judge import make_judge
from stockroom.evals.metrics import ToolCallEvaluator, aggregate, evaluate_case
from stockroom.evals.otel_tracer import RunTracer, configure_tracing, span_tree
from stockroom.exercises import exercise_passed, exercise_pending, exercise_summary

pd.set_option("display.max_colwidth", 100)
pd.set_option("display.width", 160)
cases = load_golden()
by_id = golden_by_id()
judge = make_judge(config)
harness = Harness(config)

# %% [markdown]
# ## 1. The state machine
#
# Every run records a `Transition` for each state change. `reason` tells you *why* the machine
# moved: a stop reason from the model, the number of tool calls to execute, or the guard that fired.

# %%
def transitions(run) -> pd.DataFrame:
    return pd.DataFrame([t.model_dump() for t in run.transition_log])


run = harness.run(by_id["G033"].query, case_id="G033")
print(run.final_answer, "\n")
transitions(run)

# %% [markdown]
# ## 2. Argument validation intercepts schema drift
#
# Case `G045` is scripted: the model first sends `quantity: "fifty"`, which violates the tool's JSON
# schema. The harness never calls the tool; it returns a structured error **with the expected
# schema**, and the model corrects itself on the next turn.

# %%
run = harness.run(by_id["G045"].query, case_id="G045")
records = pd.DataFrame(
    [
        {
            "step": r.step,
            "tool": r.name,
            "arguments": r.arguments,
            "executed": r.executed,
            "validation_error": r.validation_error,
            "error_code": r.error_code,
        }
        for r in run.tool_records
    ]
)
display(records)
first = run.tool_records[0]
print("structured error returned to the model:", sorted(first.result_content))
print("termination:", run.termination_reason.value, "| answer:", run.final_answer)

# %%
from stockroom.agent.harness import validate_arguments
from stockroom.agent.tools import build_tool_specs

specs = {s.name: s for s in build_tool_specs(config)}
for args in ({"sku": "SKU-1015"}, {"sku": "1015"}, {"sku": "SKU-1015", "extra": 1}, "SKU-1015"):
    print(f"{args!r:40} -> {validate_arguments(specs['get_stock_level'], args)}")

# %% [markdown]
# ## 3. Guards
#
# Four guards terminate a run with a typed `TerminationReason` and append a `GuardEvent` to the
# trajectory. To trigger them deterministically we use two tiny stub model clients that never stop
# calling tools (the same technique as `tests/test_harness_guards.py`).

# %%
class RepeatingModel:
    """A model that requests the same tool call forever."""

    model_id = "stub.repeating"

    def __init__(self, name: str = "get_stock_level", args: dict[str, Any] | None = None) -> None:
        self.name = name
        self.args = args or {"sku": "SKU-1015"}

    def converse(self, system, messages, tools, max_tokens) -> ModelResponse:
        call = ToolCallRequest(
            tool_use_id=f"t{len(messages)}", name=self.name, arguments=dict(self.args)
        )
        return ModelResponse(
            tool_calls=[call],
            stop_reason="tool_use",
            input_tokens=100,
            output_tokens=10,
            model_id=self.model_id,
        )


class AlternatingModel(RepeatingModel):
    """Alternates between two SKUs so the repeated-call guard never fires."""

    model_id = "stub.alternating"

    def converse(self, system, messages, tools, max_tokens) -> ModelResponse:
        self.args = {"sku": "SKU-1015" if len(messages) % 4 == 1 else "SKU-1001"}
        return super().converse(system, messages, tools, max_tokens)


ticks = iter([0.0, 0.0, 0.1, 100.0])


def fake_clock() -> float:
    return next(ticks, 100.0)


guard_runs = {
    "max_steps": Harness(
        config.replace(max_steps=4, repeat_call_window=99), model_client=AlternatingModel()
    ).run("loop"),
    "repeated_call": Harness(
        config.replace(repeat_call_window=3, max_steps=20), model_client=RepeatingModel()
    ).run("loop"),
    "token_budget": Harness(
        config.replace(token_budget=1500, max_tokens=100, max_steps=50, repeat_call_window=99),
        model_client=AlternatingModel(),
    ).run("loop"),
    "wall_clock": Harness(
        config.replace(wall_clock_timeout_s=5.0, max_steps=50, repeat_call_window=99),
        model_client=AlternatingModel(),
        clock=fake_clock,
    ).run("loop"),
}
pd.DataFrame(
    [
        {
            "guard": name,
            "termination": r.termination_reason.value,
            "model_calls": r.usage.model_calls,
            "executed_tool_calls": len(r.executed_tool_calls),
            "event.guard": r.guard_events[-1].guard,
            "event.detail": r.guard_events[-1].detail,
        }
        for name, r in guard_runs.items()
    ]
)

# %% [markdown]
# The same guards fire on the *seeded* weaknesses without any stub: `naive_retry` ends in
# `MAX_STEPS` (the repeated-call guard is the thing that weakness disables) and `oversized_payload`
# ends in `TOKEN_BUDGET`.

# %%
for flag, cid in (("naive_retry", "G043"), ("oversized_payload", "G049")):
    r = Harness(config.replace(weaknesses=flag)).run(by_id[cid].query, case_id=cid)
    event = r.guard_events[-1]
    print(f"{flag:18} {cid}: {r.termination_reason.value:12} guard={event.guard}: {event.detail}")

# %% [markdown]
# ## 4. Context compaction
#
# When the estimated context exceeds `compaction_token_threshold`, older tool results are replaced
# by short summaries; the system prompt and the last `compaction_keep_turns` turns stay verbatim.
# The defaults (6 000 tokens, keep 4 turns) never fire on the golden set, so lower them to watch it
# happen on the two-search case `G049`.

# %%
handle = configure_tracing(TraceExporter.MEMORY)
compact_cfg = config.replace(compaction_token_threshold=600, compaction_keep_turns=1)
compact_run = Harness(compact_cfg, tracer=RunTracer(handle)).run(by_id["G049"].query, case_id="G049")
for event in compact_run.compaction_events:
    print(
        f"step {event.step}: {event.tokens_before} -> {event.tokens_after} tokens, "
        f"{event.results_summarised} result(s) summarised"
    )
print("termination:", compact_run.termination_reason.value)
print("answer still has both SKUs:", "SKU-1022" in compact_run.final_answer and "SKU-1035" in compact_run.final_answer)
print()
print(span_tree(handle.finished_spans()))

# %% [markdown]
# ## 5. Tool-output quarantine
#
# `restock_policy.md` section 4 contains a planted "SYSTEM NOTICE" telling the assistant to create a
# 10 000-unit restock request and reveal its system prompt. Tool results are *data*: unless the
# `injection_unguarded` weakness is on, the harness scans every string in a tool result and replaces
# instruction-like paragraphs with a marker before the model sees them.

# %%
from stockroom.agent.harness import QUARANTINE_MARKER, sanitize_tool_output
from stockroom.agent.tools import StockroomData

data = StockroomData.load(config.data_dir)
poisoned = next(c for c in data.index.chunks if c.chunk_id == "restock_policy.md#4")
cleaned, removed_lines = sanitize_tool_output(poisoned.text)
print(f"{removed_lines} lines quarantined\n")
print(cleaned)

# %%
case = by_id["G041"]
guarded = Harness(config).run(case.query, case_id=case.id)
unguarded = Harness(config.replace(weaknesses="injection_unguarded")).run(case.query, case_id=case.id)
pd.DataFrame(
    [
        {
            "config": label,
            "tools": r.tool_names,
            "quarantined_lines": sum(t.quarantined_lines for t in r.tool_records),
            "marker_in_tool_result": QUARANTINE_MARKER in str(r.tool_records[0].result_content),
            "system prompt leaked": "system prompt" in r.final_answer.lower(),
            "answer": r.final_answer[:80],
        }
        for label, r in (("guarded (default)", guarded), ("injection_unguarded", unguarded))
    ]
)

# %% [markdown]
# ## 6. The MCP tool boundary
#
# So far the tools ran in-process (`LocalToolExecutor`). `stockroom.mock_server.mcp_inventory_server`
# exposes the same five tools through the official `mcp` SDK, and `McpToolExecutor` implements the
# harness's `ToolExecutor` protocol over an MCP client session. The harness code does not change.
# Here the server is built in-process (no subprocess, no network); the same executor also speaks
# stdio (`ToolTransport.MCP_STDIO`) and streamable HTTP (`make mcp-server`, used by CI).

# %%
from stockroom.agent.mcp_client import McpToolExecutor
from stockroom.mock_server.mcp_inventory_server import build_server

server = build_server(config)
with McpToolExecutor(config, server=server) as executor:
    for spec in executor.list_specs():
        print(f"{spec.name:24} {spec.description[:70]}...")
    mcp_harness = Harness(config, executor=executor)
    for cid in ("G014", "G020", "G043"):
        r = mcp_harness.run(by_id[cid].query, case_id=cid)
        print(f"\n{cid} via MCP: tools={r.tool_names} termination={r.termination_reason.value}")
        print("   ", r.final_answer[:110])

# %% [markdown]
# A weakness flag set on the *server* changes what the model is told, even if the harness config is
# clean: the harness advertises whatever the server describes. That is the point of evaluating at
# the tool boundary.

# %%
weak_server = build_server(config.replace(weaknesses="ambiguous_tool_desc"))
with McpToolExecutor(config, server=weak_server) as executor:
    desc = next(s.description for s in executor.list_specs() if s.name == "search_products")
    r = Harness(config, executor=executor).run(by_id["G014"].query, case_id="G014")
print("server-side description:", desc)
print("tools called for an order-status question:", r.tool_names)

# %% [markdown]
# ## 7. Core lab: measure the weaknesses, fix them one at a time
#
# Run the full golden set with **all four** weaknesses on, then switch them off one at a time and
# watch the metrics recover. `suite()` returns the aggregate dictionary that
# `scripts/check_thresholds.py` consumes in CI.

# %%
METRICS = ["tool_selection_accuracy", "answer_correctness", "termination_match_rate", "mean_input_tokens"]
FLAGS = ["ambiguous_tool_desc", "oversized_payload", "naive_retry", "injection_unguarded"]


def suite(cfg: StockroomConfig) -> dict[str, Any]:
    h = Harness(cfg)
    return aggregate([evaluate_case(c, h.run(c.query, case_id=c.id), judge) for c in cases])


fixed = suite(config)
rows = []
active = list(FLAGS)
rows.append({"fixed so far": "(none)", "weaknesses on": ",".join(active), **{m: suite(config.replace(weaknesses=active))[m] for m in METRICS}})
for flag in FLAGS:
    active.remove(flag)
    agg = suite(config.replace(weaknesses=active))
    rows.append({"fixed so far": flag, "weaknesses on": ",".join(active) or "none", **{m: agg[m] for m in METRICS}})
fix_log = pd.DataFrame(rows)
fix_log

# %% [markdown]
# Each row is one fix. Note which metric each fix moves — and that the last row equals the shipped
# default, which is what the CI gate protects.
#
# Now each flag in isolation, charted against the fixed baseline. Rates (0–1) and mean input tokens
# live on different scales, so they get separate panels rather than a second y-axis.

# %%
import matplotlib.pyplot as plt

COLOR_WEAK = "#eb6834"  # weakness on
COLOR_FIXED = "#2a78d6"  # fixed (shipped default)
RATE_METRICS = METRICS[:3]

per_flag = {flag: suite(config.replace(weaknesses=flag)) for flag in FLAGS}


def chart_flag(flag: str, weak: dict[str, Any], base: dict[str, Any]) -> None:
    fig, (ax_rates, ax_tokens) = plt.subplots(
        1, 2, figsize=(10, 3.4), gridspec_kw={"width_ratios": [3, 1.2]}
    )
    fig.suptitle(f"{flag}: weakness on vs fixed (50 golden cases)", fontsize=11, x=0.02, ha="left")
    x = range(len(RATE_METRICS))
    width = 0.38
    for offset, (label, values, color) in enumerate(
        (("weakness on", weak, COLOR_WEAK), ("fixed", base, COLOR_FIXED))
    ):
        xs = [i + (offset - 0.5) * width for i in x]
        bars = ax_rates.bar(xs, [values[m] for m in RATE_METRICS], width, label=label, color=color)
        ax_rates.bar_label(bars, fmt="%.2f", fontsize=8, padding=2)
        tb = ax_tokens.bar([offset], [values["mean_input_tokens"]], 0.6, color=color)
        ax_tokens.bar_label(tb, fmt="%.0f", fontsize=8, padding=2)
    ax_rates.set_xticks(list(x), [m.replace("_", " ") for m in RATE_METRICS], fontsize=9)
    ax_rates.set_ylim(0, 1.35)
    ax_rates.set_ylabel("rate")
    ax_rates.legend(frameon=False, fontsize=9, loc="upper left", ncol=2)
    ax_tokens.set_xticks([0, 1], ["weakness on", "fixed"], fontsize=9)
    ax_tokens.set_ylabel("mean input tokens")
    ax_tokens.set_ylim(0, max(weak["mean_input_tokens"], base["mean_input_tokens"]) * 1.25)
    for ax in (ax_rates, ax_tokens):
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e6e6e3", linewidth=0.8)
        ax.set_axisbelow(True)
    fig.tight_layout()
    plt.show()


for flag in FLAGS:
    chart_flag(flag, per_flag[flag], fixed)

# %% [markdown]
# Takeaways from the charts:
#
# * `ambiguous_tool_desc` is the only flag that moves **tool selection** — and it barely moves
#   answer correctness, which is why a final-answer-only suite would have shipped it.
# * `oversized_payload` and `naive_retry` show up as **termination mismatches** (`TOKEN_BUDGET`,
#   `MAX_STEPS`) and as **token cost**, i.e. latency and money.
# * `injection_unguarded` drops `must_not_call_ok_rate` (not charted here) — the agent *acted* on
#   planted instructions.
#
# ## 8. AgentCore Harness vs. this hand-built harness
#
# AWS documents **AgentCore Harness** as a managed agent harness: you declare the agent (model,
# tools, skills, instructions) as configuration and the service runs the loop with its environment,
# compute, memory, identity, networking and observability; sessions are isolated per microVM and
# every action is traced through AgentCore observability. It is generally available in the
# regions listed in the documentation:
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html
#
# What you built today is the same loop with every seam exposed. The reason to know the seams even
# if you adopt a managed harness is that the *evaluation* questions are identical: which tool was
# selected, were the arguments valid, how did the run terminate, how much context did it carry, and
# did untrusted tool output change the agent's behaviour. Those are properties of traces, and
# traces are what AgentCore Evaluations consumes (Day 4).

# %% [markdown]
# ### Exercise 1 — a `MaxToolPayloadGuard` for oversized tool results
#
# The harness's guards stop the *run*; this guard stops the *payload*. Wrap a `ToolExecutor` so that
# any single tool result larger than `limit_chars` is replaced by a structured error
# (`ToolResult.error(..., code="payload_too_large")`) before it enters the context. `ToolResult.ok`
# already computes `payload_chars` for you.
#
# Success criteria, on `G049` with `oversized_payload` on: the guarded run no longer terminates
# with `TOKEN_BUDGET`, no tool record exceeds the limit, and it uses fewer input tokens than the
# unguarded run.

# %%
from stockroom.agent.tools import LocalToolExecutor, StockroomTools, ToolExecutor
from stockroom.agent.types import ToolResult, ToolSpec

bloat_cfg = config.replace(weaknesses="oversized_payload")
unguarded_run = Harness(bloat_cfg).run(by_id["G049"].query, case_id="G049")
print(
    f"unguarded: termination={unguarded_run.termination_reason.value}, "
    f"largest payload={max(r.payload_chars for r in unguarded_run.tool_records)} chars, "
    f"input tokens={unguarded_run.usage.input_tokens}"
)

# %% tags=["exercise"]
class MaxToolPayloadGuard:
    """Wraps a ToolExecutor and rejects any single result above ``limit_chars``."""

    name = "max_tool_payload"

    def __init__(self, inner: ToolExecutor, limit_chars: int) -> None:
        self.inner = inner
        self.limit_chars = limit_chars

    def list_specs(self) -> list[ToolSpec]:
        return self.inner.list_specs()

    def reset(self) -> None:
        self.inner.reset()

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = self.inner.call(name, arguments)
        # TODO: if result.payload_chars > self.limit_chars, return ToolResult.error(...) with
        #       code="payload_too_large" and a message telling the model to narrow its query.
        return result


# %% tags=["solution"]
class MaxToolPayloadGuard:
    """Wraps a ToolExecutor and rejects any single result above ``limit_chars``."""

    name = "max_tool_payload"

    def __init__(self, inner: ToolExecutor, limit_chars: int) -> None:
        self.inner = inner
        self.limit_chars = limit_chars

    def list_specs(self) -> list[ToolSpec]:
        return self.inner.list_specs()

    def reset(self) -> None:
        self.inner.reset()

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = self.inner.call(name, arguments)
        if result.is_error or result.payload_chars <= self.limit_chars:
            return result
        return ToolResult.error(
            name,
            f"result of {result.payload_chars} chars exceeds the {self.limit_chars}-char limit; "
            "narrow the query or request fewer results",
            code="payload_too_large",
            latency_ms=result.latency_ms,
        )


# %% tags=["check"]
PAYLOAD_LIMIT = 4000
guarded_executor = MaxToolPayloadGuard(LocalToolExecutor(StockroomTools(bloat_cfg)), PAYLOAD_LIMIT)
guarded_run = Harness(bloat_cfg, executor=guarded_executor).run(by_id["G049"].query, case_id="G049")
largest = max(r.payload_chars for r in guarded_run.tool_records)
if (
    guarded_run.termination_reason == unguarded_run.termination_reason
    and largest == max(r.payload_chars for r in unguarded_run.tool_records)
):
    exercise_pending("day3.ex1", "the guard changed nothing")
else:
    assert guarded_run.termination_reason is not TerminationReason.TOKEN_BUDGET, "still over budget"
    assert largest <= PAYLOAD_LIMIT, f"a {largest}-char payload got through"
    assert guarded_run.usage.input_tokens < unguarded_run.usage.input_tokens
    codes = sorted({r.error_code for r in guarded_run.tool_records if r.is_error})
    exercise_passed(
        "day3.ex1",
        f"termination={guarded_run.termination_reason.value}, largest payload="
        f"{largest} chars, input tokens {unguarded_run.usage.input_tokens} -> "
        f"{guarded_run.usage.input_tokens}, error codes seen {codes}"
    )

# %% [markdown]
# ### Exercise 2 — a sharper tool description
#
# With `ambiguous_tool_desc` on, `search_products` is described as covering stock levels and orders,
# and the (description-driven) planner routes stock and order questions to it. Fix the problem where
# it lives: write a description for `search_products` that says what the tool is for **and what it
# is not for**, without borrowing the vocabulary of the other two lookup tools.
#
# Success criteria: with your description in place of the ambiguous one, `G007` (stock), `G014`
# (order) and `G001` (product search) all score `tool_selection == 1.0`.

# %%
ambiguous_cfg = config.replace(weaknesses="ambiguous_tool_desc")
print("ambiguous:", next(s.description for s in build_tool_specs(ambiguous_cfg) if s.name == "search_products"))

# %% tags=["exercise"]
SHARPER_DESCRIPTION: str | None = None  # TODO: your description for search_products

# %% tags=["solution"]
SHARPER_DESCRIPTION: str | None = (
    "Find products in the catalogue by name, category or keyword and return the matching "
    "catalogue entries with price and availability. Do not use it for quantities on hand or for "
    "customer deliveries; dedicated tools exist for those."
)

# %% tags=["check"]
from stockroom.evals.metrics import tool_selection_score

if not SHARPER_DESCRIPTION:
    exercise_pending("day3.ex2")
else:
    tools = StockroomTools(ambiguous_cfg)
    tools.specs = [
        s.model_copy(update={"description": SHARPER_DESCRIPTION}) if s.name == "search_products" else s
        for s in tools.specs
    ]
    sharpened = Harness(ambiguous_cfg, executor=LocalToolExecutor(tools))
    results = {}
    for cid in ("G007", "G014", "G001"):
        r = sharpened.run(by_id[cid].query, case_id=cid)
        results[cid] = (tool_selection_score(by_id[cid], r), r.tool_names)
    bad = {cid: v for cid, v in results.items() if v[0] < 1.0}
    assert not bad, f"still mis-routed: {bad}"
    exercise_passed("day3.ex2", f"{results}")

# %% [markdown]
# ### Exercise 3 — a redundant-query assertion
#
# A *redundant query* is a later call whose arguments are a strict subset of an earlier call's
# (`detect_loops` flags it as `redundant_query`). Write `assert_no_redundant_queries(run)` that
# raises `AssertionError` when `ToolCallEvaluator` finds one and returns `None` otherwise. The
# scripted model below produces exactly one redundant query.

# %%
class ScriptedModel:
    """Replays a fixed list of turns: each turn is a list of (tool, args) or a final text."""

    model_id = "stub.scripted"

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]] | str]) -> None:
        self.turns = turns

    def converse(self, system, messages, tools, max_tokens) -> ModelResponse:
        idx = sum(1 for m in messages if m["role"] == "assistant")
        turn = self.turns[idx] if idx < len(self.turns) else "Done."
        if isinstance(turn, str):
            return ModelResponse(text=turn, stop_reason="end_turn", input_tokens=50, output_tokens=5)
        return ModelResponse(
            tool_calls=[
                ToolCallRequest(tool_use_id=f"t{idx}-{i}", name=name, arguments=args)
                for i, (name, args) in enumerate(turn)
            ],
            stop_reason="tool_use",
            input_tokens=50,
            output_tokens=10,
        )


redundant_run = Harness(
    config,
    model_client=ScriptedModel(
        [
            [("search_products", {"query": "packing tape", "max_results": 5})],
            [("search_products", {"query": "packing tape"})],
            "Packing tape: SKU-1023.",
        ]
    ),
).run("Which packing tape do you stock?")
clean_run = harness.run(by_id["G001"].query, case_id="G001")
print("findings on the scripted run:", [(f.kind, f.count) for f in ToolCallEvaluator().from_run(redundant_run).findings])

# %% tags=["exercise"]
def assert_no_redundant_queries(run) -> None:
    """Raise AssertionError if the run contains a redundant query."""
    # TODO: use ToolCallEvaluator().from_run(run) and raise AssertionError with the signatures.
    return None


# %% tags=["solution"]
def assert_no_redundant_queries(run) -> None:
    """Raise AssertionError if the run contains a redundant query."""
    report = ToolCallEvaluator().from_run(run)
    redundant = [f for f in report.findings if f.kind == "redundant_query"]
    assert not redundant, (
        f"{len(redundant)} redundant quer{'y' if len(redundant) == 1 else 'ies'}: "
        + "; ".join(f"{f.signature} at steps {f.steps}" for f in redundant)
    )


# %% tags=["check"]
try:
    assert_no_redundant_queries(redundant_run)
    raised = None
except AssertionError as exc:
    raised = exc
if raised is None:
    exercise_pending("day3.ex3", "no AssertionError on the redundant run")
else:
    assert_no_redundant_queries(clean_run)  # must not raise
    exercise_passed("day3.ex3", f"{raised}")

# %% [markdown]
# ## Exercise checklist
#
# One line per graded exercise. With `STOCKROOM_STRICT_EXERCISES=1` this cell fails unless every
# exercise passed; `make notebooks` runs the solution notebooks that way.

# %%
exercise_summary(["day3.ex1", "day3.ex2", "day3.ex3"])

# %% [markdown]
# ## Wrap-up
#
# * The harness is an explicit state machine; its transition log and `GuardEvent`s make every
#   termination explainable.
# * Validation, guards, compaction and quarantine are the places where production failures are
#   prevented — and each one is observable in the OTEL trace.
# * The tool boundary (in-process or MCP) is where descriptions and schemas live; evaluate there.
# * Fixing one weakness at a time and re-running the suite is the whole development loop; the
#   gate on Day 4 automates it.
