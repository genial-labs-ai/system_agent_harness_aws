from __future__ import annotations

import importlib

from stockroom.agent.harness import Harness
from stockroom.evals import semconv as sc
from stockroom.evals.metrics import ToolCallEvaluator
from stockroom.evals.otel_tracer import (
    LoopFinding,
    TraceSummary,
    TraceToolCall,
    TracingHandle,
    detect_loops,
    span_tree,
)


def test_span_tree_per_run(harness: Harness, tracing: TracingHandle) -> None:
    query = (
        "Is SKU-1008 at or below its reorder point? "
        "If so, raise an urgent restock request for 20 units."
    )
    result = harness.run(query)
    spans = tracing.finished_spans()
    names = [s.name for s in spans]
    assert names.count("invoke_agent stockroom") == 1
    assert names.count("chat fake.stockroom-planner-v1") == 3
    assert (
        "execute_tool get_stock_level" in names and "execute_tool create_restock_request" in names
    )
    root = next(s for s in spans if s.name.startswith("invoke_agent"))
    assert root.attributes[sc.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert root.attributes[sc.STOCKROOM_TERMINATION_REASON] == "COMPLETED"
    assert format(root.context.trace_id, "032x") == result.trace_id
    chat = next(s for s in spans if s.name.startswith("chat"))
    assert chat.attributes[sc.GEN_AI_USAGE_INPUT_TOKENS] > 0
    assert chat.attributes[sc.OPENINFERENCE_SPAN_KIND] == "LLM"
    tool = next(s for s in spans if s.name == "execute_tool get_stock_level")
    assert tool.attributes[sc.GEN_AI_TOOL_NAME] == "get_stock_level"
    assert "SKU-1008" in tool.attributes[sc.GEN_AI_TOOL_CALL_ARGUMENTS]
    assert tool.parent.span_id == root.context.span_id
    assert "execute_tool" in span_tree(spans)


def test_semconv_names_match_installed_package_when_available() -> None:
    try:
        mod = importlib.import_module(
            "opentelemetry.semconv._incubating.attributes.gen_ai_attributes"
        )
    except ImportError:
        return
    for ours, theirs in [
        (sc.GEN_AI_OPERATION_NAME, "GEN_AI_OPERATION_NAME"),
        (sc.GEN_AI_REQUEST_MODEL, "GEN_AI_REQUEST_MODEL"),
        (sc.GEN_AI_USAGE_INPUT_TOKENS, "GEN_AI_USAGE_INPUT_TOKENS"),
        (sc.GEN_AI_USAGE_OUTPUT_TOKENS, "GEN_AI_USAGE_OUTPUT_TOKENS"),
        (sc.GEN_AI_TOOL_NAME, "GEN_AI_TOOL_NAME"),
        (sc.GEN_AI_TOOL_CALL_ID, "GEN_AI_TOOL_CALL_ID"),
        (sc.GEN_AI_AGENT_NAME, "GEN_AI_AGENT_NAME"),
        (sc.GEN_AI_PROVIDER_NAME, "GEN_AI_PROVIDER_NAME"),
    ]:
        if hasattr(mod, theirs):
            assert getattr(mod, theirs) == ours


def test_trace_summary_and_evaluator_agree_with_run(
    harness: Harness, tracing: TracingHandle
) -> None:
    result = harness.run("Check stock for SKU-1018 and SKU-1033.")
    summary = TraceSummary.from_spans(tracing.finished_spans(), run_id=result.run_id)
    assert (
        summary.run_id == result.run_id
        and [c.name for c in summary.tool_calls] == result.tool_names
    )
    from_spans = ToolCallEvaluator().from_spans(tracing.finished_spans(), run_id=result.run_id)
    from_run = ToolCallEvaluator().from_run(result)
    assert (
        (from_spans.total_calls, from_spans.loops)
        == (from_run.total_calls, from_run.loops)
        == (2, 0)
    )


def test_detect_loops_on_synthetic_trace() -> None:
    calls = [
        TraceToolCall("get_order_status", '{"order_id": "ORD-9001"}', i, True, 1.0, f"s{i}")
        for i in range(1, 5)
    ] + [
        TraceToolCall(
            "search_products", '{"max_results": 5, "query": "tape"}', 5, False, 1.0, "s5"
        ),
        TraceToolCall("search_products", '{"query": "tape"}', 6, False, 1.0, "s6"),
    ]
    findings = detect_loops(
        TraceSummary(run_id="r", case_id=None, termination_reason=None, tool_calls=calls), window=3
    )
    kinds = {f.kind for f in findings}
    assert kinds == {"repeated_identical_call", "loop", "redundant_query"}
    loop = next(f for f in findings if f.kind == "loop")
    assert isinstance(loop, LoopFinding) and loop.count == 4
