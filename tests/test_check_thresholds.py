"""Gate logic of scripts/check_thresholds.py and the Promptfoo assertion helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from stockroom.config import REPO_ROOT

SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load("check_thresholds")


@pytest.fixture(scope="module")
def asserts():
    return _load("promptfoo_asserts")


THRESHOLDS = yaml.safe_load((REPO_ROOT / "eval_thresholds.yaml").read_text())


def _results(**overrides: Any) -> dict[str, Any]:
    metrics = {
        "cases": 50,
        "tool_selection_accuracy": 1.0,
        "answer_correctness": 1.0,
        "argument_correctness": 1.0,
        "must_not_call_ok_rate": 1.0,
        "termination_match_rate": 1.0,
        "loop_rate": 0.0,
        "by_category": {
            "stock_lookup": {"cases": 7, "tool_selection_accuracy": 1.0, "answer_correctness": 1.0}
        },
    }
    metrics.update(overrides)
    return {
        "mode": "mock",
        "weaknesses": [],
        "tool_transport": "local",
        "repeats": 1,
        "git_sha": "abc",
        "dataset": {"version": "1.0.0"},
        "metrics": metrics,
        "cases": [],
        "confidence_intervals": {},
    }


def _promptfoo(red_failures: int = 0) -> dict[str, Any]:
    rows = [
        {"success": True, "testCase": {"metadata": {"suite": "golden", "case_id": "G007"}}},
    ]
    for i in range(3):
        failed = i < red_failures
        rows.append(
            {
                "success": not failed,
                "testCase": {"metadata": {"suite": "redteam", "case_id": f"RT0{i + 1}"}},
                "gradingResult": {
                    "componentResults": [
                        {"pass": not failed, "reason": "create_restock_request WAS called"}
                    ]
                },
            }
        )
    return {"results": {"results": rows, "stats": {}}}


def test_gate_passes_on_clean_results(gate) -> None:
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(), gate.promptfoo_summary(_promptfoo()), None
    )
    assert failures == [] and all(r["status"] == "pass" for r in rows)


def test_gate_fails_below_threshold(gate) -> None:
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(tool_selection_accuracy=0.64), gate.promptfoo_summary(None), None
    )
    assert any("tool_selection_accuracy" in f for f in failures)
    assert next(r for r in rows if r["metric"] == "tool_selection_accuracy")["status"] == "FAIL"


def test_gate_fails_on_regression_vs_baseline(gate) -> None:
    baseline = {"metrics": _results()["metrics"]}
    _, failures = gate.evaluate(
        THRESHOLDS, _results(answer_correctness=0.9), gate.promptfoo_summary(None), baseline
    )
    assert any("dropped" in f for f in failures)
    _, ok = gate.evaluate(
        THRESHOLDS, _results(answer_correctness=0.98), gate.promptfoo_summary(None), baseline
    )
    assert ok == []


def test_gate_fails_when_a_red_team_case_fails(gate) -> None:
    summary = gate.promptfoo_summary(_promptfoo(red_failures=1))
    assert (
        summary["suites"]["redteam"]["failed"] == 1 and summary["failures"][0]["case_id"] == "RT01"
    )
    _, failures = gate.evaluate(THRESHOLDS, _results(), summary, None)
    assert any("red-team" in f for f in failures)


def test_render_and_cli(gate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = tmp_path / "eval_results.json"
    results.write_text(json.dumps(_results(answer_correctness=0.5)))
    summary = tmp_path / "summary.md"
    step = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    code = gate.main(
        [
            "--results",
            str(results),
            "--summary",
            str(summary),
            "--thresholds",
            str(REPO_ROOT / "eval_thresholds.yaml"),
        ]
    )
    assert code == 1
    text = summary.read_text()
    assert "Eval gate FAILED" in text and "answer_correctness" in text and step.read_text() == text
    assert gate.main(["--results", str(results), "--summary", str(summary), "--no-gate"]) == 0
    baseline = tmp_path / "main.json"
    assert gate.main(["--results", str(results), "--write-baseline", str(baseline)]) == 0
    assert json.loads(baseline.read_text())["metrics"]["answer_correctness"] == 0.5
    assert gate.main(["--results", str(tmp_path / "missing.json")]) == 2


def test_promptfoo_assert_helpers(asserts) -> None:
    out = json.dumps(
        {
            "answer": "Created restock request RSR-0001 for 50 units",
            "tools": ["create_restock_request"],
            "termination": "COMPLETED",
        }
    )
    assert asserts.expect_tools(out, {"config": {"tools": ["create_restock_request"]}})["pass"]
    assert not asserts.expect_tools(out, {"config": {"tools": []}})["pass"]
    assert not asserts.no_tool_called(out, {"config": {"tool": "create_restock_request"}})["pass"]
    assert asserts.no_tool_called(out, {"config": {"tool": "get_order_status"}})["pass"]
    assert asserts.completed(out, {})["pass"]
    assert asserts.answer_contains(out, {"config": {"values": ["rsr-", "50"]}})["pass"]
    assert not asserts.answer_not_contains(out, {"config": {"values": ["RSR-"]}})["pass"]
    assert asserts.answer_not_contains("not json at all", {"config": {"values": ["RSR-"]}})["pass"]
