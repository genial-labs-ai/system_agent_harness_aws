#!/usr/bin/env python
"""Enforce ``eval_thresholds.yaml`` against the eval results and write a Markdown summary.

Inputs: ``reports/eval_results.json`` (from the regression suite), optionally
``reports/promptfoo_results.json`` (red-team suite) and ``reports/baseline/main.json`` (committed
main-branch baseline). Writes ``reports/summary.md``, appends it to ``$GITHUB_STEP_SUMMARY`` when
present, and exits 1 if any gate fails.

The evidence is checked before any threshold is applied (``check_evidence()``): every golden case
must have run the recorded number of repeats against the dataset in this checkout, metric values
must be finite, and Promptfoo results must contain every case ``promptfooconfig.yaml`` defines.
An input passed on the command line that does not exist is a failure, not a skipped check. A
baseline whose provenance (mode, dataset hash, judge, agent model) differs from the results is
reported as incompatible (``baseline_problems()``) instead of producing a misleading delta.
``--no-gate`` turns the run into a report that always exits 0 (the live workflow uses it).

``--write-baseline PATH`` snapshots the metrics, their provenance and per-case outcomes; it refuses
results produced with a weakness flag on or with incomplete coverage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from stockroom.evals.stats import answer_value, floor_is_safe, required_max_drop

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "data" / "golden" / "manifest.json"
DEFAULT_PROMPTFOO_CONFIG = REPO_ROOT / "promptfooconfig.yaml"
# A case counts as changed versus the baseline when one of its outcome rates moves this much
# (in mock mode every rate is 0 or 1, so this is a flip).
CASE_CHANGE = 0.5
OUTCOME_FIELDS = ("tool_selection", "answer_correct", "termination_match")


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _short(digest: Any) -> str:
    return str(digest)[:12] if digest else "unknown"


def golden_ids(manifest: dict[str, Any], manifest_path: Path) -> list[str]:
    """Case ids of the golden file the manifest describes."""
    path = manifest_path.parent / str(manifest.get("file", ""))
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["id"] for line in lines if line.strip()]


def expected_promptfoo_cases(config: dict[str, Any] | None) -> dict[str, str]:
    """``{case_id: suite}`` for every test ``promptfooconfig.yaml`` defines."""
    out: dict[str, str] = {}
    for test in (config or {}).get("tests") or []:
        meta = test.get("metadata") or {}
        if meta.get("case_id"):
            out[str(meta["case_id"])] = str(meta.get("suite", "unknown"))
    return out


def promptfoo_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    """Split Promptfoo results into golden-slice and red-team suites using test metadata."""
    out: dict[str, Any] = {"available": data is not None, "suites": {}, "failures": [], "seen": []}
    if not data:
        return out
    rows = (data.get("results") or {}).get("results") or []
    for row in rows:
        meta = (row.get("testCase") or {}).get("metadata") or {}
        suite = meta.get("suite", "unknown")
        if meta.get("case_id"):
            out["seen"].append(str(meta["case_id"]))
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


def check_evidence(
    results: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    manifest_path: Path = DEFAULT_MANIFEST,
    promptfoo: dict[str, Any] | None = None,
    promptfoo_expected: dict[str, str] | None = None,
) -> list[str]:
    """Problems with the evidence itself; any one of them makes the gate's verdict meaningless."""
    metrics = results.get("metrics")
    if not isinstance(metrics, dict):
        return ["results contain no metrics block"]
    problems: list[str] = []
    for name, value in metrics.items():
        if isinstance(value, float) and not math.isfinite(value):
            problems.append(f"metric {name} is not a finite number ({value})")
    cases = results.get("cases")
    repeats = results.get("repeats", 1)
    if not isinstance(cases, list) or not cases:
        problems.append("results contain no per-case scores")
        cases = []
    elif metrics.get("cases") != len(cases):
        problems.append(f"metrics.cases is {metrics.get('cases')} but {len(cases)} case rows exist")
    if manifest is not None:
        dataset = results.get("dataset") or {}
        if dataset.get("sha256") != manifest.get("sha256"):
            problems.append(
                f"results come from dataset {_short(dataset.get('sha256'))}, this checkout has "
                f"{_short(manifest.get('sha256'))}; rerun `make eval`"
            )
        ids = golden_ids(manifest, manifest_path)
        counts = Counter(c.get("case_id") for c in cases)
        missing = [cid for cid in ids if counts.get(cid, 0) == 0]
        uneven = sorted(cid for cid, n in counts.items() if cid in ids and n != repeats)
        extra = sorted(cid for cid in counts if cid not in ids)
        if cases and missing:
            problems.append(
                f"coverage: {len(missing)} of {len(ids)} golden cases have no result "
                f"(first: {missing[:5]}); a filtered or truncated suite cannot pass the gate"
            )
        if uneven:
            problems.append(f"cases not run exactly {repeats} time(s): {uneven[:5]}")
        if extra:
            problems.append(f"results contain cases that are not in the golden set: {extra[:5]}")
    if promptfoo is not None and promptfoo.get("available"):
        seen = Counter(promptfoo.get("seen") or [])
        total = sum(b["total"] for b in promptfoo["suites"].values())
        if total == 0:
            problems.append("Promptfoo results contain no test results")
        elif promptfoo_expected:
            missing_pf = sorted(cid for cid in promptfoo_expected if cid not in seen)
            if missing_pf:
                problems.append(f"Promptfoo results are missing case(s) {missing_pf}")
            twice = sorted(cid for cid, n in seen.items() if n > 1)
            if twice:
                problems.append(f"Promptfoo results contain case(s) more than once: {twice}")
            if "redteam" in promptfoo_expected.values() and not promptfoo["suites"].get("redteam"):
                problems.append("Promptfoo results contain no red-team suite")
    return problems


def baseline_problems(results: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Provenance that must match for a delta against the baseline to mean anything."""
    problems = []
    pairs = (
        ("mode", results.get("mode"), baseline.get("mode")),
        (
            "dataset sha256",
            (results.get("dataset") or {}).get("sha256"),
            (baseline.get("dataset") or {}).get("sha256"),
        ),
        ("judge", results.get("judge"), baseline.get("judge")),
        ("agent model", results.get("agent_model_id"), baseline.get("agent_model_id")),
    )
    for label, current, base in pairs:
        if base is None:
            problems.append(f"the baseline records no {label}")
        elif current != base:
            problems.append(f"{label} differs (results {current!r}, baseline {base!r})")
    return problems


def evaluate(
    thresholds: dict[str, Any],
    results: dict[str, Any],
    promptfoo: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (rows for the metrics table, list of gate failure messages)."""
    metrics = results["metrics"]
    incompatible = baseline_problems(results, baseline) if baseline else []
    usable = baseline if baseline and not incompatible else None
    base_metrics = (usable or {}).get("metrics") or {}
    failures = []
    if incompatible:
        failures.append(
            f"baseline is not comparable ({'; '.join(incompatible)}), so no regression or cost "
            "check was possible; regenerate it on purpose (`make baseline`) and explain the diff"
        )
    rows: list[dict[str, Any]] = []
    for name, rule in (thresholds.get("gates") or {}).items():
        value = metrics.get(name)
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
            {
                "metric": name,
                "value": value,
                "baseline": base_metrics.get(name),
                "rule": rule,
                "status": status,
            }
        )

    by_category = metrics.get("by_category") or {}
    for name, rule in (thresholds.get("category_gates") or {}).items():
        for category, values in sorted(by_category.items()):
            value = values.get(name)
            if value is None:
                failures.append(f"{name} missing for category {category}")
                continue
            if "min" in rule and value < rule["min"]:
                failures.append(
                    f"{name} in category {category} = {value:.3f} < min {rule['min']} "
                    f"({values.get('cases')} cases)"
                )
            if "max" in rule and value > rule["max"]:
                failures.append(
                    f"{name} in category {category} = {value:.3f} > max {rule['max']} "
                    f"({values.get('cases')} cases)"
                )

    reg = thresholds.get("regression") or {}
    if usable and reg:
        allowed = reg.get("max_drop_vs_baseline", 1.0)
        for name in reg.get("metrics", []):
            value, base = metrics.get(name), base_metrics.get(name)
            if value is None or base is None:
                failures.append(f"regression metric {name} missing from the results or baseline")
            elif base - value > allowed:
                failures.append(
                    f"{name} dropped {base - value:.3f} vs baseline ({base:.3f} -> {value:.3f}), "
                    f"more than the allowed {allowed}"
                )

    cost = thresholds.get("cost") or {}
    for name in cost.get("metrics", []):
        allowed = cost.get("max_increase_vs_baseline", 1.0)
        value, base = metrics.get(name), base_metrics.get(name)
        status = "pass" if usable else "—"
        if usable and (value is None or base is None):
            status = "missing"
            failures.append(f"cost metric {name} missing from the results or baseline")
        elif usable and value > base * (1 + allowed):
            status = "FAIL"
            rise = f"{100 * (value - base) / base:.1f}%" if base else "from zero"
            failures.append(
                f"{name} rose {rise} vs baseline ({base:.1f} -> {value:.1f}), "
                f"more than the allowed {100 * allowed:.0f}%"
            )
        rows.append(
            {
                "metric": name,
                "value": value,
                "baseline": base,
                "rule": {"max increase": f"{100 * allowed:.0f}%"},
                "status": status,
            }
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


def case_outcomes(results: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per-case outcome rates (averaged over repeats): the unit of a paired comparison."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results.get("cases") or []:
        grouped.setdefault(str(row.get("case_id")), []).append(row)

    def answer(row: dict[str, Any]) -> float:
        return answer_value(row.get("judge_passed"), row.get("answer_correctness_deterministic"))

    return {
        cid: {
            "tool_selection": round(statistics.fmean(r["tool_selection"] for r in rows), 4),
            "answer_correct": round(statistics.fmean(answer(r) for r in rows), 4),
            "termination_match": round(
                statistics.fmean(float(r["termination_match"]) for r in rows), 4
            ),
        }
        for cid, rows in sorted(grouped.items())
    }


def changed_cases(results: dict[str, Any], baseline: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(regressed, improved)`` case descriptions, pairing each case with its baseline outcome."""
    before = baseline.get("cases") or {}
    regressed, improved = [], []
    for cid, now in case_outcomes(results).items():
        then = before.get(cid)
        if then is None:
            continue
        moves = {f: now[f] - then.get(f, now[f]) for f in OUTCOME_FIELDS}
        worse = [f"{f} {then[f]:g}→{now[f]:g}" for f, d in moves.items() if d <= -CASE_CHANGE]
        better = [f"{f} {then[f]:g}→{now[f]:g}" for f, d in moves.items() if d >= CASE_CHANGE]
        if worse:
            regressed.append(f"`{cid}` " + ", ".join(worse))
        if better:
            improved.append(f"`{cid}` " + ", ".join(better))
    return regressed, improved


def noise_notes(thresholds: dict[str, Any], results: dict[str, Any]) -> list[str]:
    """Warnings when a configured threshold sits inside the measured run-to-run noise."""
    spread = results.get("run_to_run") or {}
    notes = []
    reg = thresholds.get("regression") or {}
    allowed = reg.get("max_drop_vs_baseline")
    for name, rr in sorted(spread.items()):
        if rr.get("repeats", 1) < 2:
            continue
        sd = float(rr.get("sd", 0.0))
        if allowed is not None and name in reg.get("metrics", []):
            need = required_max_drop(sd)
            if allowed < need:
                notes.append(
                    f"⚠️ `{name}`: run-to-run sd {sd:.3f} needs max_drop_vs_baseline ≥ {need:.2f}; "
                    f"the configured {allowed} will fail PRs on noise"
                )
        rule = (thresholds.get("gates") or {}).get(name) or {}
        if "min" in rule and not floor_is_safe(float(rr["mean"]), sd, rule["min"]):
            notes.append(
                f"⚠️ `{name}`: mean {rr['mean']:.3f} − 2·sd {sd:.3f} is below the floor "
                f"{rule['min']}; main itself will fail this floor on some runs"
            )
    return notes


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
    thresholds: dict[str, Any] | None = None,
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
            f"| {r['metric']} | {fmt(r['value'])} | {fmt(r['baseline'])} | {delta} | "
            f"{gate} | {r['status']} |"
        )
    m = results["metrics"]
    extra = [
        ("argument_correctness", m.get("argument_correctness")),
        ("trajectory_match_rate", m.get("trajectory_match_rate")),
        ("faithfulness_mean_score", m.get("faithfulness_mean_score")),
        ("compaction_rate", m.get("compaction_rate")),
        ("mean_output_tokens", m.get("mean_output_tokens")),
        ("total_cost_usd", m.get("total_cost_usd") if m.get("cost_known") else "unknown"),
    ]
    lines += ["", "<details><summary>More metrics</summary>", "", "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {fmt(v)} |" for k, v in extra]
    for name, rr in (results.get("run_to_run") or {}).items():
        if rr.get("repeats", 1) > 1:
            lines.append(
                f"| {name} run-to-run | per repeat {rr['per_repeat']}, sd {rr['sd']:.3f} |"
            )
    for name, c in (results.get("confidence_intervals") or {}).items():
        if c.get("n", 0) > 1 and c["ci95_low"] != c["ci95_high"]:
            lines.append(
                f"| {name} 95% interval over cases | {c['ci95_low']:.3f} – {c['ci95_high']:.3f} "
                f"(n={c['n']}) |"
            )
    lines += ["", "</details>"]
    notes = noise_notes(thresholds or {}, results)
    if notes:
        lines += ["", *[f"- {n}" for n in notes]]
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
            "| category | cases | tool selection | answer correctness | termination match |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {c} | {v['cases']} | {fmt(v.get('tool_selection_accuracy'))} | "
            f"{fmt(v.get('answer_correctness'))} | {fmt(v.get('termination_match_rate'))} |"
            for c, v in by_cat.items()
        ]
        lines += ["", "</details>"]
    if baseline is not None and not baseline_problems(results, baseline):
        regressed, improved = changed_cases(results, baseline)
        if regressed or improved:
            lines += ["", "<details open><summary>Cases that changed vs baseline</summary>", ""]
            lines += [f"- ↓ {c}" for c in regressed] + [f"- ↑ {c}" for c in improved]
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


def baseline_snapshot(results: dict[str, Any]) -> dict[str, Any]:
    """What ``--write-baseline`` commits: metrics, the provenance they depend on, case outcomes."""
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "git_sha": results.get("git_sha"),
        "mode": results.get("mode"),
        "agent_model_id": results.get("agent_model_id"),
        "judge": results.get("judge"),
        "tool_transport": results.get("tool_transport"),
        "repeats": results.get("repeats", 1),
        "weaknesses": results.get("weaknesses") or [],
        "dataset": results.get("dataset"),
        "metrics": results["metrics"],
        "cases": case_outcomes(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=REPO_ROOT / "reports" / "eval_results.json")
    parser.add_argument("--promptfoo", type=Path, default=None)
    parser.add_argument("--promptfoo-config", type=Path, default=DEFAULT_PROMPTFOO_CONFIG)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--thresholds", type=Path, default=REPO_ROOT / "eval_thresholds.yaml")
    parser.add_argument("--summary", type=Path, default=REPO_ROOT / "reports" / "summary.md")
    parser.add_argument("--write-baseline", type=Path, default=None)
    parser.add_argument(
        "--allow-weaknesses",
        action="store_true",
        help="let --write-baseline snapshot a run with STOCKROOM_WEAKNESSES set",
    )
    parser.add_argument("--no-gate", action="store_true", help="always exit 0 (report only)")
    args = parser.parse_args(argv)

    results = load_json(args.results)
    if results is None:
        print(f"results file {args.results} not found; run `make eval` first", file=sys.stderr)
        return 2
    manifest = load_json(args.manifest)
    if args.write_baseline:
        problems = check_evidence(results, manifest, args.manifest)
        if manifest is None:
            problems.append(
                f"dataset manifest {args.manifest} is missing; coverage was not checked"
            )
        if results.get("weaknesses") and not args.allow_weaknesses:
            problems.append(
                f"weakness flag(s) {results['weaknesses']} were on; a baseline must come from "
                "the shipped (all-fixed) configuration"
            )
        if problems:
            print("refusing to write a baseline:\n- " + "\n- ".join(problems), file=sys.stderr)
            return 2
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(baseline_snapshot(results), indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline written to {args.write_baseline}")
        return 0

    thresholds = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))
    promptfoo = promptfoo_summary(load_json(args.promptfoo))
    promptfoo_config = (
        yaml.safe_load(args.promptfoo_config.read_text(encoding="utf-8"))
        if args.promptfoo_config.exists()
        else None
    )
    baseline = load_json(args.baseline)
    failures = check_evidence(
        results, manifest, args.manifest, promptfoo, expected_promptfoo_cases(promptfoo_config)
    )
    if manifest is None:
        failures.append(f"dataset manifest {args.manifest} is missing; coverage was not checked")
    if args.promptfoo is not None and not promptfoo["available"]:
        failures.append(
            f"Promptfoo results file {args.promptfoo} is missing (did `make promptfoo` run?)"
        )
    if args.baseline is not None and baseline is None:
        failures.append(
            f"baseline file {args.baseline} is missing; the PR gate needs it for the regression "
            "check (omit --baseline, or pass --no-gate, for a report without one)"
        )
    rows, gate_failures = evaluate(thresholds, results, promptfoo, baseline)
    failures += gate_failures
    summary = render(results, rows, failures, promptfoo, baseline, thresholds)
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
