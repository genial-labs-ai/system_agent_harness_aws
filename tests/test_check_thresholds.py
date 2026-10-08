"""Gate logic of scripts/check_thresholds.py and the Promptfoo assertion helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from stockroom.config import REPO_ROOT, StockroomConfig
from stockroom.evals.report import results_document

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
CASE_IDS = ["G001", "G002", "G003"]
SHA = "ab" * 32


def _case(case_id: str, ok: bool = True, tokens: int = 2000) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": "stock_lookup",
        "tool_selection": 1.0 if ok else 0.0,
        "judge_passed": ok,
        "answer_correctness_deterministic": ok,
        "termination_match": ok,
        "input_tokens": tokens,
    }


def _results(cases: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    cases = [_case(cid) for cid in CASE_IDS] if cases is None else cases
    metrics = {
        "cases": len(cases),
        "tool_selection_accuracy": 1.0,
        "answer_correctness": 1.0,
        "argument_correctness": 1.0,
        "must_not_call_ok_rate": 1.0,
        "termination_match_rate": 1.0,
        "loop_rate": 0.0,
        "mean_input_tokens": 2000.0,
        "by_category": {
            "stock_lookup": {
                "cases": len(cases),
                "tool_selection_accuracy": 1.0,
                "answer_correctness": 1.0,
                "termination_match_rate": 1.0,
            }
        },
    }
    metrics.update(overrides)
    return {
        "mode": "mock",
        "weaknesses": [],
        "tool_transport": "local",
        "repeats": 1,
        "git_sha": "abc",
        "agent_model_id": "fake.stockroom-planner-v1",
        "judge": "fake-judge:v2",
        "dataset": {"version": "1.0.0", "sha256": SHA, "cases": len(CASE_IDS)},
        "metrics": metrics,
        "cases": cases,
        "confidence_intervals": {},
    }


def _baseline(**overrides: Any) -> dict[str, Any]:
    results = _results()
    snapshot = {k: results[k] for k in ("mode", "agent_model_id", "judge", "dataset", "metrics")}
    snapshot["cases"] = {
        cid: {"tool_selection": 1.0, "answer_correct": 1.0, "termination_match": 1.0}
        for cid in CASE_IDS
    }
    snapshot.update(overrides)
    return snapshot


@pytest.fixture
def manifest(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    golden = tmp_path / "golden.jsonl"
    golden.write_text("".join(json.dumps({"id": cid}) + "\n" for cid in CASE_IDS))
    data = {"file": golden.name, "sha256": SHA, "cases": len(CASE_IDS)}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    return data, path


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


PROMPTFOO_EXPECTED = {"G007": "golden", "RT01": "redteam", "RT02": "redteam", "RT03": "redteam"}


def test_gate_passes_on_clean_results(gate) -> None:
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(), gate.promptfoo_summary(_promptfoo()), _baseline()
    )
    assert failures == [] and all(r["status"] == "pass" for r in rows)


def test_gate_fails_below_threshold(gate) -> None:
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(tool_selection_accuracy=0.64), gate.promptfoo_summary(None), None
    )
    assert any("tool_selection_accuracy" in f for f in failures)
    assert next(r for r in rows if r["metric"] == "tool_selection_accuracy")["status"] == "FAIL"


def test_gate_fails_on_regression_vs_baseline(gate) -> None:
    _, failures = gate.evaluate(
        THRESHOLDS, _results(answer_correctness=0.9), gate.promptfoo_summary(None), _baseline()
    )
    assert any("dropped" in f for f in failures)
    _, ok = gate.evaluate(
        THRESHOLDS, _results(answer_correctness=0.98), gate.promptfoo_summary(None), _baseline()
    )
    assert ok == []


def test_gate_fails_when_a_red_team_case_fails(gate) -> None:
    summary = gate.promptfoo_summary(_promptfoo(red_failures=1))
    assert (
        summary["suites"]["redteam"]["failed"] == 1 and summary["failures"][0]["case_id"] == "RT01"
    )
    _, failures = gate.evaluate(THRESHOLDS, _results(), summary, None)
    assert any("red-team" in f for f in failures)


def test_category_gate_sees_what_the_aggregate_hides(gate) -> None:
    by_category = _results()["metrics"]["by_category"] | {
        "transient_tool_error": {"cases": 2, "termination_match_rate": 0.0}
    }
    results = _results(termination_match_rate=0.96, by_category=by_category)
    _, failures = gate.evaluate(THRESHOLDS, results, gate.promptfoo_summary(None), _baseline())
    assert failures == [
        "termination_match_rate in category transient_tool_error = 0.000 < min 1.0 (2 cases)"
    ]


def test_category_gate_applies_max_rules_too(gate) -> None:
    stock_lookup = _results()["metrics"]["by_category"]["stock_lookup"] | {"loop_rate": 0.5}
    thresholds = THRESHOLDS | {"category_gates": {"loop_rate": {"max": 0.0}}}
    results = _results(by_category={"stock_lookup": stock_lookup})
    _, failures = gate.evaluate(thresholds, results, gate.promptfoo_summary(None), _baseline())
    assert failures == ["loop_rate in category stock_lookup = 0.500 > max 0.0 (3 cases)"]


def test_cost_gate_fails_on_token_growth(gate) -> None:
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(mean_input_tokens=2300.0), gate.promptfoo_summary(None), _baseline()
    )
    assert any("mean_input_tokens rose 15.0%" in f for f in failures)
    assert next(r for r in rows if r["metric"] == "mean_input_tokens")["status"] == "FAIL"
    _, ok = gate.evaluate(
        THRESHOLDS, _results(mean_input_tokens=2150.0), gate.promptfoo_summary(None), _baseline()
    )
    assert ok == []
    from_zero = _baseline()
    from_zero["metrics"] = from_zero["metrics"] | {"mean_input_tokens": 0.0}
    _, failures = gate.evaluate(THRESHOLDS, _results(), gate.promptfoo_summary(None), from_zero)
    assert failures == [
        "mean_input_tokens rose from zero vs baseline (0.0 -> 2000.0), more than the allowed 10%"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "live"),
        ("judge", "fake-judge:v1"),
        ("agent_model_id", "some.other-model"),
        ("dataset", {"version": "1.1.0", "sha256": "cd" * 32}),
    ],
)
def test_incompatible_baseline_is_an_explicit_failure(gate, field: str, value: Any) -> None:
    baseline = _baseline(**{field: value})
    rows, failures = gate.evaluate(
        THRESHOLDS, _results(answer_correctness=0.5), gate.promptfoo_summary(None), baseline
    )
    assert any("baseline is not comparable" in f for f in failures)
    assert not any("dropped" in f for f in failures), "no delta against an incompatible baseline"
    assert all(r["baseline"] is None for r in rows)


def test_baseline_without_provenance_is_not_comparable(gate) -> None:
    legacy = _baseline()
    del legacy["judge"]
    assert gate.baseline_problems(_results(), legacy) == ["the baseline records no judge"]


def test_evidence_accepts_complete_results(gate, manifest) -> None:
    data, path = manifest
    summary = gate.promptfoo_summary(_promptfoo())
    assert gate.check_evidence(_results(), data, path, summary, PROMPTFOO_EXPECTED) == []


def test_evidence_rejects_a_truncated_suite(gate, manifest) -> None:
    data, path = manifest
    problems = gate.check_evidence(_results(cases=[_case("G001")]), data, path)
    assert any("coverage: 2 of 3 golden cases have no result" in p for p in problems)


def test_evidence_rejects_uneven_repeats_and_strange_cases(gate, manifest) -> None:
    data, path = manifest
    cases = [_case("G001"), _case("G001"), _case("G002"), _case("G003"), _case("G999")]
    problems = gate.check_evidence(_results(cases=cases), data, path)
    assert any("not run exactly 1 time(s): ['G001']" in p for p in problems)
    assert any("not in the golden set: ['G999']" in p for p in problems)


def test_evidence_rejects_another_dataset_and_non_finite_metrics(gate, manifest) -> None:
    data, path = manifest
    results = _results(answer_correctness=float("nan"))
    results["dataset"]["sha256"] = "cd" * 32
    problems = gate.check_evidence(results, data, path)
    assert any("results come from dataset cdcdcdcdcdcd" in p for p in problems)
    assert any("answer_correctness is not a finite number" in p for p in problems)


def test_evidence_rejects_empty_or_incomplete_promptfoo(gate, manifest) -> None:
    data, path = manifest
    empty = gate.promptfoo_summary({"results": {"results": []}})
    assert gate.check_evidence(_results(), data, path, empty, PROMPTFOO_EXPECTED) == [
        "Promptfoo results contain no test results"
    ]
    partial = _promptfoo()
    partial["results"]["results"] = partial["results"]["results"][:2]
    problems = gate.check_evidence(
        _results(), data, path, gate.promptfoo_summary(partial), PROMPTFOO_EXPECTED
    )
    assert problems == ["Promptfoo results are missing case(s) ['RT02', 'RT03']"]


def test_changed_cases_pairs_each_case_with_its_baseline(gate) -> None:
    results = _results(cases=[_case("G001"), _case("G002", ok=False), _case("G003")])
    baseline = _baseline()
    baseline["cases"]["G003"]["termination_match"] = 0.0
    regressed, improved = gate.changed_cases(results, baseline)
    assert regressed == ["`G002` tool_selection 1→0, answer_correct 1→0, termination_match 1→0"]
    assert improved == ["`G003` termination_match 0→1"]


def test_noise_notes_warn_when_a_threshold_sits_inside_the_noise(gate) -> None:
    results = _results()
    results["run_to_run"] = {
        "answer_correctness": {
            "per_repeat": [0.9, 0.86, 0.94],
            "mean": 0.9,
            "sd": 0.04,
            "repeats": 3,
        }
    }
    notes = gate.noise_notes(THRESHOLDS, results)
    assert any("needs max_drop_vs_baseline ≥ 0.12" in n for n in notes)
    assert any("below the floor 0.85" in n for n in notes)
    results["run_to_run"]["answer_correctness"]["repeats"] = 1
    assert gate.noise_notes(THRESHOLDS, results) == []


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data))
    return path


def test_render_and_cli(gate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest) -> None:
    _, manifest_path = manifest
    results = _write(tmp_path / "eval_results.json", _results(answer_correctness=0.5))
    summary = tmp_path / "summary.md"
    step = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    common = ["--results", str(results), "--summary", str(summary)]
    common += ["--manifest", str(manifest_path)]
    assert gate.main(common) == 1
    text = summary.read_text()
    assert "Eval gate FAILED" in text and "answer_correctness" in text and step.read_text() == text
    assert gate.main([*common, "--no-gate"]) == 0
    baseline = tmp_path / "main.json"
    assert gate.main([*common, "--write-baseline", str(baseline)]) == 0
    written = json.loads(baseline.read_text())
    assert written["metrics"]["answer_correctness"] == 0.5
    assert written["judge"] == "fake-judge:v2" and set(written["cases"]) == set(CASE_IDS)
    assert gate.main(["--results", str(tmp_path / "missing.json")]) == 2


def test_cli_fails_when_a_requested_input_is_missing(gate, tmp_path: Path, manifest) -> None:
    _, manifest_path = manifest
    results = _write(tmp_path / "eval_results.json", _results())
    summary = tmp_path / "summary.md"
    args = ["--results", str(results), "--summary", str(summary), "--manifest", str(manifest_path)]
    assert gate.main(args) == 0
    assert gate.main([*args, "--baseline", str(tmp_path / "nope.json")]) == 1
    assert "baseline file" in summary.read_text()
    assert gate.main([*args, "--baseline", str(tmp_path / "nope.json"), "--no-gate"]) == 0
    assert gate.main([*args, "--promptfoo", str(tmp_path / "nope.json")]) == 1


def test_write_baseline_refuses_weakened_or_incomplete_runs(gate, tmp_path: Path, manifest) -> None:
    _, manifest_path = manifest
    out = tmp_path / "main.json"
    weak = _results()
    weak["weaknesses"] = ["naive_retry"]
    args = ["--manifest", str(manifest_path), "--write-baseline", str(out)]
    assert gate.main(["--results", str(_write(tmp_path / "w.json", weak)), *args]) == 2
    assert not out.exists()
    assert (
        gate.main(
            ["--results", str(_write(tmp_path / "w.json", weak)), *args, "--allow-weaknesses"]
        )
        == 0
    )
    truncated = _write(tmp_path / "t.json", _results(cases=[_case("G001")]))
    assert gate.main(["--results", str(truncated), *args]) == 2
    # Without a manifest, coverage cannot be checked, so nothing is written.
    elsewhere = tmp_path / "other.json"
    no_manifest = ["--manifest", str(tmp_path / "gone.json"), "--write-baseline", str(elsewhere)]
    assert gate.main(["--results", str(truncated), *no_manifest]) == 2
    assert not elsewhere.exists()


def test_an_empty_suite_yields_results_the_gate_rejects(gate) -> None:
    doc = results_document(StockroomConfig.mock(), [], judge_label="fake-judge:v2")
    assert doc["metrics"] == {"cases": 0} and doc["run_to_run"] == {}
    assert gate.check_evidence(doc) == ["results contain no per-case scores"]


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
