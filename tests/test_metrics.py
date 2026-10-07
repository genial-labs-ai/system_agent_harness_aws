from __future__ import annotations

from stockroom.agent.harness import Harness
from stockroom.agent.types import ToolCallRecord
from stockroom.config import StockroomConfig
from stockroom.evals.golden import ExpectedTool, GoldenCase
from stockroom.evals.judge import FakeJudge
from stockroom.evals.metrics import (
    OutputEvaluator,
    aggregate,
    args_match,
    argument_correctness_score,
    evaluate_case,
    match_calls,
    tool_selection_score,
)


def rec(name: str, **args) -> ToolCallRecord:
    return ToolCallRecord(step=1, tool_use_id="t", name=name, arguments=args)


def test_args_match_rules() -> None:
    assert args_match(
        {"query": "ear defenders"}, {"query": "stock ear defenders", "max_results": 5}
    )
    assert not args_match({"query": "ear defenders"}, {"query": "ear"})
    assert args_match({"quantity": 50}, {"quantity": 50.0, "sku": "x"})
    assert not args_match({"sku": "SKU-1001"}, {"sku": "SKU-1002"})
    assert not args_match({"sku": "SKU-1001"}, "not a dict")


def test_match_modes() -> None:
    expected = [
        ExpectedTool(name="get_stock_level", args={"sku": "SKU-1008"}),
        ExpectedTool(name="create_restock_request", args={"sku": "SKU-1008"}),
    ]
    actual = [
        rec("get_stock_level", sku="SKU-1008"),
        rec("search_policy_docs", query="x"),
        rec("create_restock_request", sku="SKU-1008", quantity=20),
    ]
    assert match_calls(expected, actual, "exact")[0] is False
    assert match_calls(expected, actual, "in_order_subset")[0] is True
    assert match_calls(expected, actual, "any_order")[0] is False
    assert match_calls(expected, actual[::2][::-1], "any_order")[0] is True


def test_case_scores_and_aggregate() -> None:
    case = GoldenCase(
        id="T001",
        category="stock_lookup",
        query="How many units of SKU-1015 are in stock?",
        expected_tools=[ExpectedTool(name="get_stock_level", args={"sku": "SKU-1015"})],
        expected_facts=["42"],
        forbidden_facts=["420"],
        reference_answer="42 units",
        max_steps=2,
    )
    run = Harness(StockroomConfig.mock()).run(case.query)
    scores = evaluate_case(case, run, FakeJudge("v2"))
    assert scores.tool_selection == 1.0 and scores.argument_correctness == 1.0
    assert scores.answer_correctness_deterministic and scores.judge_passed and scores.step_count_ok
    check = OutputEvaluator.evaluate(case, run)
    assert check.passed and check.facts_found == ["42"]
    agg = aggregate([scores, scores])
    assert (
        agg["cases"] == 2
        and agg["tool_selection_accuracy"] == 1.0
        and "stock_lookup" in agg["by_category"]
    )
    wrong = case.model_copy(
        update={"expected_tools": [ExpectedTool(name="search_products", args={"query": "gloves"})]}
    )
    assert tool_selection_score(wrong, run) == 0.0 and argument_correctness_score(wrong, run) == 0.0
