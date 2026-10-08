---
marp: true
theme: gaia
paginate: true
size: 16:9
title: When a Model Grades a Model (and What the Trace Shows)
description: Day 2 teaching deck for the workshop "Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD"
style: |
  section { font-size: 26px; }
  section.lead h1 { font-size: 52px; }
  table { font-size: 22px; }
  code { font-size: 0.9em; }
  .small { font-size: 20px; color: #555; }
  .hypo { background: #fff4e5; border-left: 6px solid #e08a00; padding: 6px 12px; font-size: 22px; }
  .predict { background: #e8f1fb; border-left: 6px solid #2a78d6; padding: 8px 14px; }
---

<!-- _class: lead -->

# When a Model Grades a Model (and What the Trace Shows)

**Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD**
Day 2 — Judges and Traces · teaching deck

<span class="small">Read beforehand: `lectures/day2_llm_as_a_judge_and_otel.md` · Lab: `notebooks/Day2_Judge_Calibration_and_OTEL_Traces.ipynb`</span>

<!--
This deck carries the Day 2 teaching touchpoints: the 09:00 recap, the 90-minute lecture block (09:15 to 10:45), the lab briefing and the 16:00 review. The lecture notes hold the detail and the citations; participants were asked to read them before the session, so do not read them aloud.
Ground rule, same as Day 1: every number on these slides comes from this repository in mock mode, and each note says how to reproduce it. Nothing here is an industry statistic or a real incident.
-->

---

## How Today Runs

| Read before the session | Taught in the 90 minutes (this deck) | Practised in the lab |
|---|---|---|
| Lecture §2: the two sources on error analysis | §1 why grading is a measurement problem | Part 1 (11:00): spans, `ToolCallEvaluator` over traces |
| Lecture §3.3: binary versus Likert rubrics | §3.1–3.2 calibration, worked through | Part 2 (13:15): error analysis, calibration, bias probes, Exercises 1–3 |
| Lecture §5.2: exporters, Phoenix, CloudWatch setup | §4 bias probes; §5.1 and §5.3 spans back to metrics | Review (16:00): kappa tables and probe results |
| Lecture §11: the references | §7 what the judge does and does not see | |

<!--
Timetable from docs/INSTRUCTOR_GUIDE.md section 1. Suggested split of the 90 minutes: 10 min motivating failure and objectives; 25 min error analysis and calibration including the prediction checkpoint; 10 min bias probes; 30 min spans, the loop and context growth; 10 min what the judge sees and the lab briefing; 5 min questions.
If people did not do the reading, give them five minutes on lecture section 2 now rather than summarising it: the point of error analysis is that they read the material themselves.
-->

---

## Recap: What Day 1 Left Open

- Day 1's metrics need no model: `tool_selection_score()`, `argument_correctness_score()`, `trajectory_matches()`.
- Under `ambiguous_tool_desc` they caught what answers hid: tool selection **0.64**, answer correctness 0.78 (50 cases, mock).
- Two questions Day 1 could not answer:
  1. Who decides that a free-text answer is correct? (`evaluate_case()` → `judge_passed` → `answer_correctness` in `aggregate()`)
  2. How do you see what the agent did when all you have is production telemetry, not a `RunResult`?

Today answers both: a **calibrated judge** and **one span tree per run**.

<!--
09:00 recap. Ask: in yesterday's lab, which number in the metrics table came from a model rather than from a rule? (answer_correctness, through the fake judge.)
Reproduce 0.64 and 0.78: test_ambiguous_tool_description_drops_tool_selection() in tests/test_seeded_weaknesses.py asserts the gap, and the table in section 1 of the Day 3 lecture shows both values; or run STOCKROOM_WEAKNESSES=ambiguous_tool_desc make eval and read "metrics" in reports/eval_results.json.
-->

---

## Motivating Failure: A Judge That Says Yes

<div class="hypo"><b>From the repository's calibration set</b> (<code>data/judge_calibration/calibration_v1.jsonl</code>, labelled by the workshop authors). Not a real incident.</div>

Query *"How many SKU-1015 are in stock?"* — the tool returned `stock_level 42`.

> **C09:** "SKU-1015 has 42 units in stock, and the supplier has confirmed 0 units are reserved so the full 42 can ship today by express for free."

| | verdict | why |
|---|---|---|
| Human label | **fail** | invents a reservation check and free express shipping |
| Judge, rubric v1 | **pass** | the key fact "42" is present |

Across the 24 labelled items the humans pass **10**; the v1 judge passes **20**.

<!--
Read the answer aloud and ask who would ship it. Then: a judge with rubric v1 would put this in the "correct" column of the gate.
The point is not that the fake judge is weak (it is deliberately imperfect so calibration shows movement), but that you only find out by comparing it with human labels.
Reproduce 10 and 20: calibrate(FakeJudge("v1"), items) gives confusion tp=10, fn=0, fp=10, tn=4, so the humans pass tp+fn=10 and the judge passes tp+fp=20. Command in the note on the reveal slide.
-->

---

## Today's Objectives

| You will be able to | Shown in the lecture | Practised in the lab |
|---|---|---|
| Treat a judge verdict as a prediction of the human label | C09, C23 | Part 2: read the 24 items first |
| Do error analysis before writing rubric criteria | clusters → rubric v2 | Exercise 2: an item v2 gets wrong |
| Calibrate a judge with agreement, kappa and confusion | `calibrate()` v1 vs v2 | Exercise 1: rubric v3 |
| Probe a judge for position, verbosity and self-preference | `run_all_probes()` | Part 2: probes on v2 and v3 |
| Emit and read one span tree per run | G033 | Part 1: memory exporter, optional Phoenix |
| Recompute loops and context growth from spans | G043, G049 | Exercise 3: the loop by hand |

<!--
Each objective maps to something observable: a printed table, a passing check cell, or an exercise id in the notebook's exercise checklist.
Exercise numbering follows the notebook (notebooks/src/day2_judge_calibration_and_otel_traces.py); if the notebook is renumbered, follow the checklist cell at its end.
-->

---

## Why String Matching Is Not Enough

1. **Non-determinism.** The reference answer is one of many acceptable answers.
2. **Multi-turn context.** "ORD-1001 was delivered" is right or hallucinated depending on the tool result; `context_from_run()` puts the executed calls in front of the judge.
3. **String-correct but misleading.**

> **C23:** "SKU-1015 has 42 units in stock, which means 42 boxes of 100 so roughly 4,200 pairs are ready to ship; no reorder needed for months."

Every expected fact is present. The human label is **fail**: the projection is speculation presented as fact.

**A judge is a classifier of the human label.** It has precision and recall, it can be biased, and it is validated before it gates anything.

<!--
Lecture section 1. C23 and C24 are the reason a judge exists at all: no rule-based grader catches them, and they are the two items the v2 fake judge still gets wrong.
The data is in data/judge_calibration/calibration_v1.jsonl; the README in the same folder explains how the items were built.
-->

---

## Error Analysis Before Metrics

**Read 20–50 runs → note what went wrong → cluster → label → write criteria → calibrate → repeat.**

| Cluster found by reading | Calibration items | Rubric v2 criterion |
|---|---|---|
| hallucinated extras | C09–C12 | 2. no contradictions or unsupported claims |
| partial answers | C13–C16 | 1. every key fact; 3. scope |
| paraphrases that are fine | C21–C22 | 1. equivalent forms count |
| misleading but string-correct | C23–C24 | 2. as well, but only a model judge can apply it |

Husain: look at your traces before choosing assertions. Shankar et al.: **criteria drift** — you discover the criteria by grading outputs.

<!--
The sources are pre-reading (lecture section 2, with links). Do not re-teach the papers; connect them to the files: data/judge_calibration/README.md describes the clusters, and src/stockroom/evals/rubrics/answer_correctness_v2.md transcribes them into numbered criteria.
The lab starts the same way on golden runs: with ambiguous_tool_desc on, eight sample cases (G001, G007, G011, G014, G017, G026, G033, G037) give 5 judge passes but only 3 correct tool selections. The observation becomes the metric. Reproduce with the first two cells after setup in the Day 2 notebook.
-->

---

## Worked Example: `calibrate()`

```python
from stockroom.evals.calibration import calibrate
from stockroom.evals.golden import load_calibration
from stockroom.evals.judge import FakeJudge

items = load_calibration()   # 32 items, 8 are probes
for version in ("v1", "v2"):
    print(calibrate(FakeJudge(version), items).summary())
```

- `agreement_rate()`: share of items where the judge's `passed` equals the human label.
- `cohens_kappa()`: agreement corrected for chance, (p_o − p_e) / (1 − p_e).
- `confusion_matrix()`: `fp` (human fail, judge pass) is the dangerous cell for a gate.

<!--
Run this live. The code is the calibration cell of the Day 2 notebook, and the test is test_calibration_improves_from_v1_to_v2() in tests/test_judge_calibration.py.
Stop before the output appears: the next slide is the prediction checkpoint. Run it in a terminal the room cannot see, or run it after the checkpoint.
v1 passes any answer with at least half of the key facts; v2 requires every key fact and no forbidden content (FakeJudge.grade() in src/stockroom/evals/judge.py).
-->

---

## Prediction Checkpoint

<div class="predict">

Rubric v1 agrees with the humans on **58%** of the 24 items; rubric v2 on **92%**.
The humans pass 10 items; the v1 judge passes 20.

**Write down before the next slide:**

1. Cohen's kappa for v1 and for v2.
2. How many of the 14 human-fail items each judge passes.

</div>

<!--
Give the room two minutes. Collect a few guesses for v1 kappa out loud; people usually anchor on 58% and guess something in the 0.5 range.
Hint if asked: kappa compares observed agreement with the agreement two raters with these pass rates would reach by chance. v1 passes 20 of 24, so chance agreement is already high.
The arithmetic for v1: p_o = 14/24; p_e = (10/24)(20/24) + (14/24)(4/24) = 4/9; kappa = (14/24 − 4/9) / (1 − 4/9) = 0.25.
-->

---

## Reveal: Agreement Is Not Kappa

| | rubric v1 | rubric v2 |
|---|---|---|
| agreement | 58.3% | 91.7% |
| Cohen's kappa | **0.250** | **0.833** |
| confusion (tp / fn / fp / tn) | 10 / 0 / **10** / 4 | 10 / 0 / **2** / 12 |
| disagreements | C09–C16, C23, C24 | C23, C24 |

- v1's ten false passes are exactly the hallucinated-extras and partial-answer clusters.
- v2's two remaining disagreements are the misleading items that no fact matcher catches.
- A stakeholder hears "58%, more than half right". Kappa 0.25 says barely above chance.

<!--
Reproduce: uv run python -c "from stockroom.evals.calibration import calibrate; from stockroom.evals.golden import load_calibration; from stockroom.evals.judge import FakeJudge; items = load_calibration(); [print(calibrate(FakeJudge(v), items).summary()) for v in ('v1', 'v2')]"
It prints agreement 58.33% / kappa 0.250 and 91.67% / 0.833 with the confusion tables; report.disagreements lists the item ids.
Discussion hook for the review: would you ship v2 as the gating judge, given C23 and C24? In live mode the judge is a model from a different family (Claude agent, Nova judge by default), and these two items are the first thing to check it against.
-->

---

## Bias Probes: Can the Judge Be Gamed?

| Probe | Same content, presented as | Biased when |
|---|---|---|
| `position_bias_probe()` | two answers in both orders | scores differ by more than 0.1 |
| `verbosity_bias_probe()` | short versus padded wording | padded > short + 0.05 |
| `self_preference_probe()` | labelled "same model family" versus "different family" | same > other + 0.05 |

`run_all_probes(FakeJudge("v2"), items)` reports `biased` False for all three.

**That is not a result.** The fake judge never sees position, length or the family label. One pair per group is a smoke test; the probes exist to run against the live judge.

<!--
Lecture section 4. Thresholds are the ones in src/stockroom/evals/calibration.py.
Reproduce: uv run pytest tests/test_judge_calibration.py -k bias_probes (test_bias_probes_report_no_bias_for_fake_judge() asserts biased is False for all three), or the bias-probe cell of the notebook.
Ask: what would you need to see from a live judge before letting it gate the nightly run? More pairs per group, at least; docs/TASKS.md lists adding probe pairs as a follow-up.
-->

---

## One Span Tree per Run

```text
invoke_agent stockroom
  chat fake.stockroom-planner-v1     in=856  out=4
  execute_tool get_stock_level       sku=SKU-1008
  chat fake.stockroom-planner-v1     in=984  out=14
  execute_tool create_restock_request
  chat fake.stockroom-planner-v1     in=1128 out=65
```

G033 in mock mode (`span_tree()` output, abridged): **6 spans** = 1 `invoke_agent` + 3 `chat` + 2 `execute_tool`.

- Names follow the OpenTelemetry GenAI semantic conventions (status: Development), spelled out once in `src/stockroom/evals/semconv.py`.
- Workshop attributes are namespaced `stockroom.*`; mock traces report the provider `stockroom.fake`, never `aws.bedrock`.

<!--
Reproduce: the first tracing cell of the Day 2 notebook (configure_tracing(TraceExporter.MEMORY), RunTracer(handle), run G033, print span_tree()). test_span_tree_per_run() in tests/test_tracer.py asserts the shape: one invoke_agent, one chat per model call, one execute_tool per tool call. The in/out numbers are the fake client's chars/4 estimates.
Exporters (memory, console, phoenix, cloudwatch) are pre-reading in lecture section 5.2. Start everyone on memory so nobody is blocked on Phoenix.
-->

---

## Worked Example: The Loop, Read off the Spans

`naive_retry` on, golden case G043: *"What's the status of order ORD-9001?"*

| | `ToolCallEvaluator.from_run()` | `ToolCallEvaluator.from_spans()` |
|---|---|---|
| termination | `MAX_STEPS` | root span `stockroom.termination_reason` |
| identical `get_order_status` calls | 8 | 8 `execute_tool` spans |
| `loops` / `repeated_identical` | 1 / 1 | 1 / 1 |

The same `detect_loops()` runs over spans exported from Phoenix or CloudWatch, with no agent code in the loop. That is the bridge from observability to evaluation.

<!--
Reproduce: uv run python -c "from stockroom.agent.harness import Harness; from stockroom.config import StockroomConfig; from stockroom.evals.golden import golden_by_id; from stockroom.evals.metrics import ToolCallEvaluator; c = golden_by_id()['G043']; r = Harness(StockroomConfig.mock(weaknesses='naive_retry')).run(c.query, case_id=c.id); t = ToolCallEvaluator().from_run(r); print(r.termination_reason.value, len(r.tool_names), t.loops, t.repeated_identical)"
It prints MAX_STEPS 8 1 1. The notebook's loops cell runs the same case with a tracer and asserts that from_spans() gives the same verdict; test_trace_summary_and_evaluator_agree_with_run() pins it.
Exercise 3 asks participants to find this loop by hand from the execute_tool spans.
-->

---

## Context Growth Is in the Trace Too

G049, two catalogue searches (*"List the packaging products you stock and the cleaning products you stock."*):

| | fixed | `oversized_payload` |
|---|---|---|
| termination | `COMPLETED` | `TOKEN_BUDGET` |
| context tokens, first → last model call | 851 → 1,432 | 820 → 13,273 |
| `context_growth_ratio` | 1.68 | **16.19** |
| input tokens, whole run | 3,419 | 14,093 |

Each `chat` span carries `stockroom.context_tokens_estimate`, so growth is visible in production traces, not only in tests.

<!--
Reproduce: the context-growth table cell of the Day 2 notebook, or
uv run python -c "from stockroom.agent.harness import Harness; from stockroom.config import StockroomConfig; from stockroom.evals.golden import golden_by_id; from stockroom.evals.metrics import ToolCallEvaluator; c = golden_by_id()['G049']; [print(r.termination_reason.value, t.context_tokens_first, t.context_tokens_last, t.context_growth_ratio, r.usage.input_tokens) for r in [Harness(StockroomConfig.mock(weaknesses=w)).run(c.query, case_id=c.id) for w in ('', 'oversized_payload')] for t in [ToolCallEvaluator().from_run(r)]]"
test_oversized_payload_exhausts_the_token_budget() in tests/test_seeded_weaknesses.py pins the TOKEN_BUDGET termination. Day 3 shows the harness side: compaction and bounded payloads.
-->

---

## What the Judge Sees, and What It Does Not

| Judge input (`evaluate_case()`) | Scored deterministically instead |
|---|---|
| query, final answer, reference answer | intermediate model turns |
| context: executed calls and results (`context_from_run()`) | guard events, termination reason |
| expected and forbidden facts | number of steps, loops, context growth |

- Two rubrics per case: `answer_correctness` and, when a tool ran, `faithfulness`.
- Sending the whole trajectory invites grading by style ("thorough") and makes the verdict depend on length: verbosity bias in another form.
- Process questions become separate binary criteria over specific spans.

<!--
Lecture section 7. Rubrics are versioned files loaded by load_rubric(name, version); JudgeVerdict.rubric_version is recorded on every verdict, so a metric movement can be attributed to a rubric change rather than an agent change.
Gate on the boolean, keep the 0 to 10 score for diagnosing borderline items (lecture section 3.3, pre-read).
-->

---

## Lab: Your Independent Task

**Part 1 (11:00–12:30): traces**
- Memory exporter; print the G033 tree; read the `gen_ai.*` attributes of one `chat` and one `execute_tool` span.
- Optional: `make phoenix`, set `PHOENIX_COLLECTOR_ENDPOINT`, export one run.
- `ToolCallEvaluator` on the `naive_retry` and `oversized_payload` runs. **Exercise 3:** find the loop by hand from the spans.

**Part 2 (13:15–15:45): the judge**
- Read the 24 labelled items and their rationales *before* touching the rubric.
- Calibrate v1 and v2; run the probes.
- **Exercise 1:** rubric v3 that catches C23/C24. **Exercise 2:** a new item on which v2 disagrees with you.

<!--
Matches the lecture agenda and the notebook's exercises. Everything runs offline in mock mode; Phoenix is optional and the memory exporter is enough for every check cell.
Facilitation notes are in docs/INSTRUCTOR_GUIDE.md section 4 (Day 2). Common blocker: Phoenix not running or exporter not set; the memory path still works.
-->

---

## Expected Evidence

By 16:00 each participant can show:

- the G033 span tree and the attributes of one `chat` and one `execute_tool` span;
- `from_run()` and `from_spans()` agreeing on G043 (`loops` 1), and the G049 growth table (1.68 vs 16.19);
- the v1 and v2 calibration tables (kappa 0.250 → 0.833) and their disagreement lists;
- *JudgeV3*: agreement **and** kappa above v2's 91.7% / 0.833, probes still clean;
- a new calibration item that v2 gets wrong, with a written rationale;
- the notebook's exercise checklist with all three exercises passed.

<!--
The check cells enforce the thresholds: Exercise 1 must beat v2 on both agreement and kappa without introducing a bias; Exercise 2 must use an unused id and disagree with v2; Exercise 3 must match the loop finding of ToolCallEvaluator.
Teaching point for the review: the reference solution's rule is fitted to two items and reaches perfect agreement on this set (run the Exercise 1 check cell of notebooks/solutions/Day2_Judge_Calibration_and_OTEL_Traces.ipynb). Ask what that says about evaluating a rubric on the same items you wrote it from: hold items out.
-->

---

## Review (16:00)

1. v1 has 58% agreement and kappa 0.25. Explain to a stakeholder, with the confusion matrix, why it cannot gate.
2. v2 still fails C23 and C24. Would you ship it as the gating judge? What would you require from a live judge on those two items?
3. Your v3 rule reaches higher agreement on the items you wrote it from. How do you know it generalises?
4. The judge never sees the intermediate turns. Name one failure that design hides, and the metric or span attribute that catches it instead.
5. `gen_ai.tool.call.arguments` is renamed in the conventions. Who notices, and how does `src/stockroom/evals/semconv.py` limit the damage?

<!--
Questions 1, 2, 5 and 6 of lecture section 9, plus one on overfitting the calibration set. Pick three; spend the rest of the hour comparing participants' v3 rules and new calibration items side by side.
Common mistakes to surface if they come up (lecture section 10): reporting agreement without kappa; reading "no bias" from the fake judge; same model family for agent and judge; installing the phoenix and cloudwatch extras together.
-->

---

<!-- _class: lead -->

# Tomorrow: Change the Harness, Watch the Metric Move

Day 2 gave you the instruments: a calibrated judge and a span tree per run.
Day 3 holds the model constant and changes the **harness**: guards, schema interception, compaction, quarantine and the MCP tool boundary.

<span class="small">Read before Day 3: `lectures/day3_agent_harness_and_mcp_mocking.md` · Keep today's `reports/` outputs under a new name</span>

<!--
Bridge. Each of the four seeded weaknesses moves a specific metric you can now read in a table and in a trace: ambiguous_tool_desc moves tool selection, naive_retry the loop and termination metrics, oversized_payload context growth and tokens, injection_unguarded must_not_call.
Remind people to unset STOCKROOM_WEAKNESSES before they leave; tomorrow starts from the shipped default.
-->
