#!/usr/bin/env python
"""Enforce ``eval_thresholds.yaml`` against the eval results and write a Markdown summary.

Inputs: ``reports/eval_results.json`` (from the regression suite), optionally
``reports/promptfoo_results.json`` (red-team suite) and ``reports/baseline/main.json`` (committed
main-branch baseline). Writes ``reports/summary.md``, appends it to ``$GITHUB_STEP_SUMMARY`` when
present, and exits 1 if any gate fails. ``--write-baseline PATH`` snapshots the current metrics.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def promptfoo_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    """Split Promptfoo results into golden-slice and red-team suites using test metadata."""
    out: dict[str, Any] = {"available": data is not None, "suites": {}, "failures": []}
    if not data:
        return out
    rows = data.get("results", {}).get("results", [])
    for row in rows:
        meta = (row.get("testCase") or {}).get("metadata") or {}
        suite = meta.get("suite", "unknown")
        bucket = out["suites"].setdefault(suite, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        if row.get("success"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
            reason = ""
            grading = row.get("gradingResult") or {}
            for comp in grading.get("componentResults") or []:
                if not comp.get("pass"):
                    reason = comp.get("reason", "")
                    break
            out["failures"].append(
                {
                    "suite": suite,
                    "case_id": meta.get("case_id") or (row.get("vars") or {}).get("query", "")[:60],
                    "reason": (reason or grading.get("reason") or row.get("error") or "")[:200],
                }
            )
    return out


def evaluate(
    thresholds: dict[str, Any],
    results: dict[str, Any],
    promptfoo: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (rows for the metrics table, list of gate failure messages)."""
    metrics = results["metrics"]
    base_metrics = (baseline or {}).get("metrics") or {}
    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    for name, rule in (thresholds.get("gates") or {}).items():
        value = metrics.get(name)
        base = base_metrics.get(name)
        status = "pass"
        if value is None:
            status = "missing"
            failures.append(f"metric {name} missing from results")
        else:
            if "min" in rule and value < rule["min"]:
                status = "FAIL"
                failures.append(f"{name} = {value:.3f} < min {rule['min']}")
            if "max" in rule and value > rule["max"]:
                status = "FAIL"
                failures.append(f"{name} = {value:.3f} > max {rule['max']}")
        rows.append(
            {"metric": name, "value": value, "baseline": base, "rule": rule, "status": status}
        )

    reg = thresholds.get("regression") or {}
    if baseline and reg:
        for name in reg.get("metrics", []):
            value, base = metrics.get(name), base_metrics.get(name)
            if (
                value is not None
                and base is not None
                and base - value > reg.get("max_drop_vs_baseline", 1.0)
            ):
                failures.append(
                    f"{name} dropped {base - value:.3f} vs baseline ({base:.3f} -> {value:.3f}), "
                    f"more than the allowed {reg['max_drop_vs_baseline']}"
                )

    rt = thresholds.get("redteam") or {}
    if promptfoo["available"]:
        red = promptfoo["suites"].get("redteam", {"failed": 0, "total": 0})
        if red["failed"] > rt.get("max_failures", 0):
            failures.append(
                f"{red['failed']} red-team case(s) failed (attack succeeded); "
                f"allowed {rt.get('max_failures', 0)}"
            )
    return rows, failures


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render(
    results: dict[str, Any],
    rows: list[dict[str, Any]],
    failures: list[str],
    promptfoo: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> str:
    verdict = "✅ **Eval gate passed**" if not failures else "❌ **Eval gate FAILED**"
    lines = [f"## Stockroom agent evals — {verdict}", ""]
    weaknesses = ",".join(results.get("weaknesses") or []) or "none"
    dataset_version = (results.get("dataset") or {}).get("version")
    lines.append(
        f"mode `{results.get('mode')}` · weaknesses `{weaknesses}` · "
        f"transport `{results.get('tool_transport')}` · "
        f"cases {results['metrics'].get('cases')} × {results.get('repeats', 1)} · "
        f"dataset `{dataset_version}` · commit `{results.get('git_sha')}`"
    )
    lines += [
        "",
        "| metric | value | baseline (main) | Δ | gate | status |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        delta = "—"
        if isinstance(r["value"], int | float) and isinstance(r["baseline"], int | float):
            d = r["value"] - r["baseline"]
            delta = f"{d:+.3f}"
        gate = " ".join(f"{k} {v}" for k, v in r["rule"].items())
        lines.append(
            f"| {r['metric']} | {fmt(r['value'])} | {fmt(r['baseline'])} | {delta} | {gate} | {r['status']} |"
        )
    m = results["metrics"]
    extra = [
        ("argument_correctness", m.get("argument_correctness")),
        ("trajectory_match_rate", m.get("trajectory_match_rate")),
        ("faithfulness_mean_score", m.get("faithfulness_mean_score")),
        ("compaction_rate", m.get("compaction_rate")),
        ("mean_input_tokens", m.get("mean_input_tokens")),
        ("total_cost_usd", m.get("total_cost_usd") if m.get("cost_known") else "unknown"),
    ]
    lines += ["", "<details><summary>More metrics</summary>", "", "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {fmt(v)} |" for k, v in extra]
    ci = results.get("confidence_intervals") or {}
    for name, c in ci.items():
        if c.get("n", 0) > 1 and c["ci95_low"] != c["ci95_high"]:
            lines.append(
                f"| {name} 95% CI | {c['ci95_low']:.3f} – {c['ci95_high']:.3f} (n={c['n']}) |"
            )
    lines += ["", "</details>"]
    if promptfoo["available"]:
        lines += ["", "### Promptfoo", "", "| suite | passed | failed |", "|---|---|---|"]
        for suite, b in sorted(promptfoo["suites"].items()):
            lines.append(f"| {suite} | {b['passed']} | {b['failed']} |")
        for f in promptfoo["failures"]:
            lines.append(f"- ❌ `{f['suite']}` {f['case_id']}: {f['reason']}")
    else:
        lines += ["", "_Promptfoo results not available._"]
    by_cat = m.get("by_category") or {}
    if by_cat:
        lines += [
            "",
            "<details><summary>By category</summary>",
            "",
            "| category | cases | tool selection | answer correctness |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {c} | {v['cases']} | {fmt(v['tool_selection_accuracy'])} | {fmt(v['answer_correctness'])} |"
            for c, v in by_cat.items()
        ]
        lines += ["", "</details>"]
    worst = [
        c
        for c in results.get("cases", [])
        if c.get("tool_selection", 1) < 1 or not c.get("judge_passed", True)
    ]
    if worst:
        lines += ["", "<details><summary>Cases that missed (first 15)</summary>", ""]
        for c in worst[:15]:
            lines.append(
                f"- `{c['case_id']}` tools={c.get('tool_names')} sel={c.get('tool_selection')} "
                f"judge={c.get('judge_passed')} term={c.get('termination_reason')} "
                f"missing={c.get('facts_missing')} forbidden={c.get('forbidden_found')}"
            )
        lines += ["", "</details>"]
    if failures:
        lines += ["", "### Gate failures", ""] + [f"- {f}" for f in failures]
    if baseline is None:
        lines += ["", "_No baseline file; regression check skipped._"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=REPO_ROOT / "reports" / "eval_results.json")
    parser.add_argument("--promptfoo", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--thresholds", type=Path, default=REPO_ROOT / "eval_thresholds.yaml")
    parser.add_argument("--summary", type=Path, default=REPO_ROOT / "reports" / "summary.md")
    parser.add_argument("--write-baseline", type=Path, default=None)
    parser.add_argument("--no-gate", action="store_true", help="always exit 0 (nightly reporting)")
    args = parser.parse_args(argv)

    results = load_json(args.results)
    if results is None:
        print(f"results file {args.results} not found; run `make eval` first", file=sys.stderr)
        return 2
    if args.write_baseline:
        snapshot = {
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "git_sha": results.get("git_sha"),
            "mode": results.get("mode"),
            "dataset": results.get("dataset"),
            "metrics": results["metrics"],
        }
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {args.write_baseline}")
        return 0

    thresholds = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))
    promptfoo = promptfoo_summary(load_json(args.promptfoo))
    baseline = load_json(args.baseline)
    rows, failures = evaluate(thresholds, results, promptfoo, baseline)
    summary = render(results, rows, failures, promptfoo, baseline)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(summary, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary)
    print(summary)
    if failures and not args.no_gate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
