from __future__ import annotations

import asyncio

import pytest

from stockroom.config import StockroomConfig
from stockroom.evals.calibration import (
    calibrate,
    cohens_kappa,
    confusion_matrix,
    position_bias_probe,
    run_all_probes,
)
from stockroom.evals.judge import (
    FakeJudge,
    JudgeInput,
    StockroomDeepEvalLLM,
    StockroomRagasLLM,
    extract_key_facts,
    fact_present,
    load_rubric,
    normalize,
    parse_geval_test_case,
    parse_json_object,
)


def test_fact_matching_is_boundary_aware() -> None:
    assert fact_present("42", "there are 42 units")
    assert not fact_present("42", "there are 420 units")
    assert not fact_present("125", "we hold 1,250 units")
    assert fact_present("1250", "we hold 1,250 units")
    assert fact_present("30 days", "returned within thirty days")
    assert fact_present("$8.50", "costs $8.50 per order")
    assert normalize("Thirty  Days, 1,250") == "30 days, 1250"


def test_extract_key_facts() -> None:
    facts = extract_key_facts(
        "ORD-1001 has shipped with DPD, tracking DP48213377, ETA 2026-09-15, 2 items"
    )
    assert "ORD-1001" in facts and "shipped" in facts and "2026-09-15" in facts
    assert "2" not in facts  # single digits are ignored


def test_rubrics_exist_and_differ() -> None:
    v1, v2 = load_rubric("answer_correctness", "v1"), load_rubric("answer_correctness", "v2")
    assert "Contradictions" in v2 and "Contradictions" not in v1
    assert "faithfulness" in load_rubric("faithfulness", "v1").lower()
    with pytest.raises(FileNotFoundError):
        load_rubric("answer_correctness", "v9")


def test_fake_judge_v1_vs_v2() -> None:
    item = JudgeInput(
        query="Status?",
        answer="ORD-1001 shipped and was delivered yesterday.",
        expected_facts=["shipped", "DP48213377"],
        forbidden_facts=["delivered"],
    )
    assert FakeJudge("v1").grade(item).passed  # half the facts is enough for v1
    v2 = FakeJudge("v2").grade(item)
    assert not v2.passed and "DP48213377" in v2.rationale and "forbidden" in v2.rationale
    faithful = FakeJudge("v2").grade(
        JudgeInput(
            query="q",
            answer="SKU-1015 has 42 units. It is in aisle 3.",
            context="SKU-1015 stock_level 42 aisle 3",
            rubric="faithfulness",
        )
    )
    assert faithful.passed and faithful.score == 1.0


def test_calibration_improves_from_v1_to_v2(calibration_items) -> None:
    v1 = calibrate(FakeJudge("v1"), calibration_items)
    v2 = calibrate(FakeJudge("v2"), calibration_items)
    assert v1.n == v2.n == 24  # probes excluded
    assert v1.kappa < 0.5 < v2.kappa
    assert v2.agreement >= 0.9
    assert {d[0] for d in v2.disagreements} == {"C23", "C24"}  # the deliberately subtle items
    assert v1.confusion.fp > v2.confusion.fp  # v1 is lenient


def test_kappa_and_confusion_math() -> None:
    assert cohens_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohens_kappa([True, True, False, False], [True, False, True, False]) == 0.0
    cm = confusion_matrix([True, False], [False, False])
    assert (cm.tp, cm.fn, cm.fp, cm.tn) == (0, 1, 0, 1)


def test_bias_probes_report_no_bias_for_fake_judge(calibration_items) -> None:
    probes = run_all_probes(FakeJudge("v2"), calibration_items)
    assert {p.probe for p in probes} == {"position", "verbosity", "self_preference"}
    assert not any(p.biased for p in probes)
    assert position_bias_probe(FakeJudge("v1"), calibration_items)[0].biased is False


def test_parse_helpers() -> None:
    assert parse_json_object('Sure! ```json\n{"score": 8, "pass": true}\n```') == {
        "score": 8,
        "pass": True,
    }
    assert parse_json_object("no json here") == {}
    prompt = (
        "Evaluation Steps:\n1. x\n\nTest Case:\nInput:\nQ? \n\nActual Output:\nA. \n\n"
        "Expected Output:\nE. \n\nParameters:\nInput, Actual Output"
    )
    assert parse_geval_test_case(prompt) == {
        "Input": "Q?",
        "Actual Output": "A.",
        "Expected Output": "E.",
    }


def test_deepeval_bridge_runs_geval_offline(mock_config: StockroomConfig) -> None:
    from deepeval.metrics import GEval, ToolCorrectnessMetric
    from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

    llm = StockroomDeepEvalLLM(mock_config, FakeJudge("v2"))
    metric = GEval(
        name="Answer correctness",
        criteria="Contains every key fact of the expected output without contradictions.",
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=llm,
        threshold=0.7,
        async_mode=False,
    )
    expected = "SKU-1015 has 42 units in stock (reorder point 10); aisle 3, bin 2."
    metric.measure(
        LLMTestCase(
            input="q",
            actual_output="SKU-1015: 42 units, reorder point 10, aisle 3.",
            expected_output=expected,
        )
    )
    assert metric.is_successful() and metric.score == 1.0
    metric.measure(
        LLMTestCase(input="q", actual_output="SKU-1015 has 420 units.", expected_output=expected)
    )
    assert not metric.is_successful()
    tool_metric = ToolCorrectnessMetric(model=llm)
    tool_metric.measure(
        LLMTestCase(
            input="q",
            actual_output="a",
            tools_called=[ToolCall(name="get_stock_level", input_parameters={"sku": "SKU-1015"})],
            expected_tools=[ToolCall(name="get_stock_level", input_parameters={"sku": "SKU-1015"})],
        )
    )
    assert tool_metric.score == 1.0


def test_ragas_bridge_runs_collections_metrics_offline(mock_config: StockroomConfig) -> None:
    from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness

    llm = StockroomRagasLLM(mock_config, FakeJudge("v2"))
    ctx = ["Unused items may be returned within 30 days of delivery for a full refund."]

    async def go():
        good = await Faithfulness(llm=llm).ascore(
            user_input="window?",
            response="Unused items can be returned within 30 days for a full refund.",
            retrieved_contexts=ctx,
        )
        bad = await Faithfulness(llm=llm).ascore(
            user_input="window?",
            response="Items can be returned within 90 days.",
            retrieved_contexts=ctx,
        )
        precision = await ContextPrecision(llm=llm).ascore(
            user_input="window?",
            reference="Returns within 30 days.",
            retrieved_contexts=[*ctx, "Standard shipping is $8.50."],
        )
        recall = await ContextRecall(llm=llm).ascore(
            user_input="window?",
            reference="Unused items can be returned within 30 days.",
            retrieved_contexts=ctx,
        )
        return good.value, bad.value, precision.value, recall.value

    good, bad, precision, recall = asyncio.run(go())
    assert good == 1.0 and bad == 0.0
    assert precision == pytest.approx(1.0, abs=1e-6) and recall == 1.0
