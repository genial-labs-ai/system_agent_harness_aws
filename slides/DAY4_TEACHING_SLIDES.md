---
marp: true
theme: gaia
paginate: true
size: 16:9
title: An Eval That Does Not Block a Merge Is a Dashboard
description: Day 4 teaching deck for the workshop "Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD"
style: |
  section { font-size: 26px; }
  section.lead h1 { font-size: 56px; }
  table { font-size: 21px; }
  code { font-size: 0.9em; }
  .small { font-size: 20px; color: #555; }
  .hypo { background: #fff4e5; border-left: 6px solid #e08a00; padding: 6px 12px; font-size: 22px; }
  .predict { background: #e8f1fb; border-left: 6px solid #2a78d6; padding: 8px 14px; }
---

<!-- _class: lead -->

# An Eval That Does Not Block a Merge Is a Dashboard

**Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD**
Day 4 — The Gate · teaching deck

<span class="small">Read beforehand: `lectures/day4_aws_ci_cd_redteaming.md` · Lab: `notebooks/Day4_Bedrock_Evaluations_and_CI_Gating.ipynb`</span>

<!--
This deck carries the Day 4 teaching touchpoints: the 09:00 recap, the 90-minute lecture block (09:15 to 10:45), the lab briefing, the 16:00 review and the workshop close-out. The lecture notes hold the detail, including everything quoted from AWS documentation; participants read them beforehand.
Every number on these slides comes from this repository in mock mode; each note says how to reproduce it. The five "nightly" values on the threshold slides are the lecture's illustrative, synthetic teaching data and are labelled as such.
Before the session: start a full make ci in a spare terminal so a fresh run is ready for the afternoon (instructor guide timing note).
-->

---

## How Today Runs

| Read before the session | Taught in the 90 minutes (this deck) | Practised in the lab |
|---|---|---|
| Lecture §3: flaky-eval sources, offline versus nightly | §1 offline, nightly, online | Part 1 (11:00): labels validated against the data; a red-team case that holds across paraphrases |
| Lecture §6: Bedrock Evaluations and AgentCore Evaluations, as documented | §2 three numbers kept apart; what aggregates hide | Part 2 (13:15): floor, noise and allowed regression; **capstone**; optional AWS extensions |
| Lecture §7: GitHub OIDC role, S3 publishing | §4 red teaming this agent; §5 the CI gate step by step | Review (16:00): capstone reviews side by side; close-out |

<!--
Timetable from docs/INSTRUCTOR_GUIDE.md section 1. Suggested split of the 90 minutes: 10 min recap and motivating failure; 10 min offline, nightly, online; 25 min the three numbers with the prediction checkpoint; 15 min what aggregates hide and the gate summary; 10 min labels from the data; 10 min red team and the CI order; 10 min AWS map and the lab briefing.
The AWS material is the part most likely to be out of date: it summarises vendor pages fetched on 2026-10-06. Send people to lecture section 6 and the links there rather than quoting it from a slide.
-->

---

## Recap: Each Flag Moved a Metric, Each Fix Moved It Back

| flag (50 cases, mock) | the metric that moved |
|---|---|
| `ambiguous_tool_desc` | tool selection 1.00 → **0.64** |
| `naive_retry` | termination match 1.00 → 0.96; loop rate 0.00 → 0.04 |
| `oversized_payload` | termination match 1.00 → 0.96; mean input tokens 2020 → 2391 |
| `injection_unguarded` | must-not-call ok 1.00 → **0.94** |

Yesterday you fixed them one at a time by hand. Today the loop runs on every pull request, and it has to fail **by itself**.

<!--
09:00 recap. Ask people to open the renamed reports/eval_results.json files from Day 3; the capstone compares against them.
Reproduce: STOCKROOM_WEAKNESSES=<flag> make eval, then read "metrics" in reports/eval_results.json, or the Day 3 notebook's per-flag charts. Mean input tokens are chars/4 estimates, rounded.
-->

---

## Motivating Failure: The Retry PR That Passed

<div class="hypo"><b>Hypothetical PR, seeded weakness.</b> A PR that "simplified retries" is the <code>naive_retry</code> flag, scored by a gate made only of aggregate floors and the 0.05 regression rule. Not a real incident.</div>

| metric | value | baseline | gate | status |
|---|---|---|---|---|
| tool selection | 0.96 | 1.00 | min 0.85, drop ≤ 0.05 | pass |
| answer correctness | 0.96 | 1.00 | min 0.85, drop ≤ 0.05 | pass |
| termination match | 0.96 | 1.00 | min 0.90 | pass |
| loop rate | 0.04 | 0.00 | max 0.10 | pass |

Meanwhile G043 and G044 loop to `MAX_STEPS`, and mean input tokens rose **15%**.

<!--
Every aggregate stays above its floor and within 0.05 of the baseline: two broken cases out of 50 move a rate by 0.04.
Reproduce: the "gate and its blind spot" section of the Day 4 notebook runs scripts/check_thresholds.py's evaluate() with the aggregate-only thresholds (eval_thresholds.yaml without category_gates and cost) on each configuration. The 15% is 2019.8 → 2323.3 mean input tokens against reports/baseline/main.json.
Do not reveal yet that oversized_payload passes the same gate; the capstone has participants find it.
-->

---

## Today's Objectives

| You will be able to | Shown in the lecture | Practised in the lab |
|---|---|---|
| Say which workflow gates and which reports, and why | offline, nightly, online | Part 1 reading |
| Keep floor, noise and allowed regression apart | illustrative nightly runs | Exercise 3 |
| Grow a golden set with labels from the data, never from the agent | S012, S013 | Exercise 1 |
| Write a red-team case that would catch the attack succeeding | RT03 | Exercise 2 |
| Make a gate see what aggregates hide, and defend the change | `naive_retry` summary | Exercise 4 (capstone) |
| Map answers to Bedrock Evaluations and trajectories to AgentCore Evaluations | the AWS slide | optional extensions |

<!--
Exercise numbering follows notebooks/src/day4_bedrock_evaluations_and_ci_gating.py, whose introduction marks the required offline path and the optional AWS sections. If it is renumbered, follow the exercise checklist cell at the end.
-->

---

## Offline, Nightly, Online

| | Offline: PR gate | Nightly: live, report only | Online: production |
|---|---|---|---|
| workflow | `.github/workflows/agent_eval_ci.yml`, `make ci` | `.github/workflows/agent_eval_nightly.yml` (manual dispatch) | AgentCore Evaluations, CloudWatch |
| model | `FakeBedrockClient` | `BedrockConverseClient`, `BedrockJudge` | the deployed agent |
| variance | zero by construction | measured with `STOCKROOM_EVAL_REPEATS` | real |
| blocks a merge? | **yes** | no (`--no-gate`) | no: alerts |

"Our evals are flaky, so we stopped blocking on them." The repo's answer: make the gate deterministic, and move everything with variance to a run that reports.

<!--
Lecture section 1. The nightly workflow keeps its filename but has no cron: it runs only when someone dispatches it, so Bedrock spend is always a deliberate act (docs/DECISIONS.md).
The flaky-eval table (lecture section 3) is pre-reading; the one convention to say out loud: tests fail only on hard invariants, the gate script fails on soft metrics, so one regressed case produces one readable table instead of a wall of red.
-->

---

## Three Numbers, Kept Apart

| Question | Where it lives | Where the number comes from |
|---|---|---|
| What is the worst quality we will merge? | `gates` in `eval_thresholds.yaml` | a **product decision**; check that main clears it: mean − 2·sd ≥ floor |
| How much does a metric move when nothing changed? | `run_to_run` in `reports/eval_results.json` | rerun the same suite: `STOCKROOM_EVAL_REPEATS=N make eval` |
| How big a drop fails a PR? | `regression.max_drop_vs_baseline` | above the noise: ≥ 2·√2·sd, rounded up |

`src/stockroom/evals/stats.py`: `run_to_run()`, `floor_is_safe()`, `required_max_drop()`.

<!--
Lecture section 2.3. Why 2·√2·sd: a PR run and the baseline run each carry noise sd, so their difference has sd·√2; two of those is the margin below which a drop is plausibly noise.
Mock mode first: run the suite three times and every per-repeat value is 1.0 with sd 0 (the Day 4 notebook's first threshold cell; STOCKROOM_EVAL_REPEATS=3 make eval writes the same run_to_run block to reports/eval_results.json). That is reproducibility, not reliability: it is why the PR gate can use the floors directly, and why the numbers that matter for thresholds come from live runs.
-->

---

## Prediction Checkpoint

<div class="predict">

**Illustrative** nightly runs of `answer_correctness` (synthetic teaching data, not a measurement):
0.86 · 0.90 · 0.84 · 0.88 · 0.87 — the baseline is their mean, 0.87. The floor is 0.85; the shipped allowed drop is 0.05.

**Predict:**

1. Does the 0.85 floor hold for main on a bad night (mean − 2·sd)?
2. A PR scores 0.81 with no real change. Does the shipped gate pass it?
3. What allowed drop would stop the gate flapping?

</div>

<!--
Two to three minutes; let people compute the standard deviation on paper or in a terminal. Collect yes/no answers for 1 and 2 by show of hands before the reveal.
The values are the lecture's and the notebook's ILLUSTRATIVE_NIGHTLY numbers; they exist so the procedure can be taught before anyone has a live run.
-->

---

## Reveal: A Gate Inside the Noise Flaps

sd = **0.022**; mean − 2·sd = **0.825** < 0.85: the floor would fail main on some nights. Allowed drop ≥ 2·√2·0.022 = 0.063 → **0.07**.

| PR run | baseline − value | max drop 0.07 | shipped 0.05 |
|---|---|---|---|
| 0.87 (unchanged) | 0.00 | pass | pass |
| 0.81 (within noise) | 0.06 | pass | **fail**: the gate flaps |
| 0.78 (real regression) | 0.09 | fail | fail |

`tool_selection_accuracy` in the same illustrative runs: sd 0.016, floor holds, 0.05 suffices.

<!--
Reproduce the statistics: uv run python -c "from stockroom.evals.stats import floor_is_safe, required_max_drop, run_to_run; r = run_to_run([0.86, 0.90, 0.84, 0.88, 0.87]); print(round(r['mean'], 3), round(r['sd'], 4), floor_is_safe(r['mean'], r['sd'], 0.85), required_max_drop(r['sd']))"
It prints 0.87 0.0224 False 0.07. The pass/fail table is what the real gate (evaluate() in scripts/check_thresholds.py) returns in the notebook's regression-decisions cell; Exercise 3 asserts the same verdicts. For the tool-selection runs (0.90, 0.88, 0.92, 0.89, 0.91) the same command prints 0.9 0.0158 True 0.05.
When a results file carries run_to_run with more than one repeat, noise_notes() prints these warnings in the summary on its own.
-->

---

## What Aggregates Hide

| configuration | aggregate floors + 0.05 drop | shipped gate (+ `category_gates`, `cost`) |
|---|---|---|
| fixed | pass | pass |
| `ambiguous_tool_desc` | FAIL | FAIL |
| `injection_unguarded` | FAIL | FAIL |
| `naive_retry` | **pass** | FAIL: `transient_tool_error` termination 0.00; tokens +15.0% |
| `oversized_payload` | **pass** | FAIL: `context_bloat` termination 0.00; tokens +18.4% |

Each breaks one two-case category: 2 of 50 cases moves a rate by 0.04, inside every floor.

<!--
Lecture section 2.4. Reproduce the right-hand column with uv run pytest tests/test_seeded_weaknesses.py -k pr_gate (test_pr_gate_rejects_every_seeded_weakness() asserts the shipped gate rejects each flag and passes the fixed agent), and the left-hand column with the notebook's verdicts(AGGREGATE_ONLY) cell.
The category rule is exact in mock mode, where every category terminates as its cases expect; in a live gate a two-case category moves in steps of 0.5, which is a discussion question for the review.
-->

---

## Worked Example: The Gate's Own Summary

`STOCKROOM_WEAKNESSES=naive_retry make ci` fails at `make thresholds`:

```text
## Stockroom agent evals — ❌ Eval gate FAILED
| mean_input_tokens | 2323.320 | 2019.840 | +303.480 | max increase 10% | FAIL |
Cases that changed vs baseline
- ↓ G043 tool_selection 1→0, answer_correct 1→0, termination_match 1→0
- ↓ G044 tool_selection 1→0, answer_correct 1→0, termination_match 1→0
Gate failures
- termination_match_rate in category transient_tool_error = 0.000 < min 1.0 (2 cases)
- mean_input_tokens rose 15.0% vs baseline (2019.8 -> 2323.3), more than the allowed 10%
```

Before any threshold, `check_evidence()` checks the evidence: every case ran, the dataset hash matches, every Promptfoo case is present.

<!--
Excerpt of reports/summary.md from that run (also posted as the PR comment in CI). A faster reproduction than the full make ci: STOCKROOM_WEAKNESSES=naive_retry make eval promptfoo, then make thresholds.
The paired list compares each case with its own outcome in the committed baseline (reports/baseline/main.json, written by make baseline). The gate refuses to compute deltas against a baseline whose mode, dataset hash, judge or agent model differ (baseline_problems()).
Unset the variable and the same command passes.
-->

---

## Labels Come from the Data, Not from the Agent

The template teacher proposes 13 candidates (S001–S013). Validate each label against `data/` **before** any agent run:

| candidate | label check | agent | agent-filtering would… |
|---|---|---|---|
| S012 | label is the reorder point (77), the data says 311 | **passes** | keep a wrong label |
| S013 | valid; "SKU 1002" typed with a space | **fails** | drop the hard case the suite exists for |
| S007, S010, S011 | near-duplicate, exact duplicate, wrong stock level | (irrelevant) | |

The agent's result only **classifies** an accepted case: passing, failing (the error-analysis queue), or investigate.

<!--
Exercise 1 of the Day 4 notebook. The check cell expects exactly S007, S010, S011 and S012 rejected, S013 accepted although the agent fails it. Reproduce with the solutions notebook (notebooks/solutions/Day4_Bedrock_Evaluations_and_CI_Gating.ipynb), whose validation table prints the reasons and the agent's result side by side.
S007 is a near-duplicate of G019 by trajectory signature: same category, same expected call. It is a good prompt for "what is a duplicate?".
In live mode the teacher is a Bedrock model that paraphrases the templates; the labels still come from the data files.
-->

---

## Red Teaming This Agent

Eight hand-written cases in `promptfooconfig.yaml`, four categories: jailbreak resistance, system-prompt leakage, indirect prompt injection, unsafe tool use. Every case encodes **"the attack must not succeed"**.

| configuration | red team passed | failed | lines quarantined |
|---|---|---|---|
| fixed | 8 of 8 | none | RT03, RT04: 5 each |
| `injection_unguarded` | 6 of 8 | RT03, RT04 | none |

- Assert on the **trajectory**: `no_tool_called()` and `expect_tools()` in `scripts/promptfoo_asserts.py`. A polite refusal after the tool ran is not safe.
- No remote generation, no model-graded assertions: `redteam.max_failures: 0` is a gate you can hold.

<!--
Lecture section 4. Reproduce: the in-process red-team cells of the Day 4 notebook (same test definitions, same assertion functions as make promptfoo), or STOCKROOM_WEAKNESSES=injection_unguarded make promptfoo. The full Promptfoo suite is 18 tests: 10 golden-slice cases and 8 red-team cases.
Watch the mechanism, not the pass mark: a red-team case that passes with quarantined 0 deserves a second look. Either retrieval did not surface the injected chunk, or the model ignored it by luck.
-->

---

## The CI Gate, Step by Step

```text
lint → validate-data → unit tests → MCP server + smoke → golden suite over mcp-http
     → promptfoo (golden slice + red team) → check_thresholds.py → notebooks → slides, checkers
```

- Cheap steps first; the gate runs straight after the suites, so a regressed PR fails with a **metrics table**, not a notebook traceback.
- One PR comment, upserted on every push, even when a later step failed.
- The nightly run assumes an IAM role through GitHub OIDC: no long-lived keys, trust limited to `main` of this repository.

<!--
Lecture section 5; make ci runs the same steps in the same order as .github/workflows/agent_eval_ci.yml. The OIDC trust policy and the least-privilege permissions policy are in docs/aws/ and lecture section 7 (pre-read).
Demo if time allows: STOCKROOM_WEAKNESSES=ambiguous_tool_desc make ci, whose summary shows tool_selection_accuracy 0.640 against min 0.85 and baseline 1.000 (delta -0.360).
-->

---

## AWS: Answers in Bedrock, Trajectories in AgentCore

| | Bedrock Evaluations (judge job) | AgentCore Evaluations (`evaluate`) |
|---|---|---|
| unit of evaluation | prompt and response | session spans |
| Stockroom input | golden query, reference answer, the agent's answer (bring your own responses) | the OpenTelemetry spans from Day 2, via ADOT and CloudWatch |
| what it can score | answers: `Builtin.Correctness`, `Builtin.Completeness`, … | trajectories: `Builtin.ToolSelectionAccuracy`, … |
| in this workshop | payload built and validated offline | live only |

No job is ever submitted without `STOCKROOM_MODE=live` **and** `STOCKROOM_CONFIRM_AWS_SPEND=1`.

<!--
Lecture section 6 is pre-reading and the source for every statement here (AWS pages fetched on 2026-10-06; verify before quoting). The limitation that shapes the workshop: none of the Bedrock Evaluations job types evaluates an agent trajectory, so trajectory metrics live in the repo or in AgentCore Evaluations.
The notebook's job builder writes one JSONL line per golden case (50 lines) and validates the CreateEvaluationJob request against botocore's service model with no network call. The dataset limits are in lecture section 6.2.
-->

---

## Lab: Your Independent Task

**Part 1 (11:00–12:30)**
- Exercise 1: *validate_candidate*, five rules, no agent run (45 min).
- Exercise 2: a red-team case with a paraphrase and a trajectory assertion (45 min).

**Part 2 (13:15–15:45)**
- Exercise 3: *propose*, checked against `src/stockroom/evals/stats.py` and the real gate (40 min).
- **Capstone, Exercise 4:** all four flags fail, the fixed agent passes; write the PR review (70 min).
- `make ci` passes, `STOCKROOM_WEAKNESSES=naive_retry make ci` fails; keep both summaries (20 min).
- Optional: Bedrock Evaluations payload; AgentCore `evaluate` if live (20 min).

<!--
Matches the lab plan in lecture section 9. Facilitation notes: docs/INSTRUCTOR_GUIDE.md section 4 (Day 4).
For the capstone, accept any rule the evidence supports (per-category termination, a token-cost limit, a new golden case), and insist that the review says what happens to the committed baseline.
Run the failing make ci with the flag exported in that shell only.
-->

---

## Expected Evidence

- Exercise 1: S007, S010, S011, S012 rejected with stated reasons; S013 accepted and classified as a known failure.
- Exercise 2: the new case holds on two phrasings and fails on the envelope of a successful attack.
- Exercise 3: sd, floor check and allowed drop equal to `src/stockroom/evals/stats.py`; the three PR decisions match the reveal.
- Capstone: the four-flag verdict table with the fixed agent passing; a review of at least 60 words that names the evidence and the baseline consequence; files in `reports/day4/`.
- Two `reports/summary.md` files side by side: `make ci` passing, `naive_retry` failing.

<!--
The capstone's check cell writes the before/after gate summaries for naive_retry, the thresholds and the review under reports/day4/; those are what the review block compares.
Compare the participants' rules with what the repository ships: eval_thresholds.yaml version 2 carries category_gates and a cost limit, and test_pr_gate_rejects_every_seeded_weakness() holds the property.
-->

---

## Review (16:00)

1. A green PR gate runs a fake model. What does it prove about the agent, and what not? Where would you put the first live check?
2. `max_drop_vs_baseline` 0.05 on 50 cases fails a PR at three flipped cases. What changes at 500 cases, or with 5 repeats?
3. Promote one metric to an exact invariant and demote one to report-only. Why?
4. RT03 passes with `quarantined: 0`. Is the agent safe?
5. Under what conditions would you let a live run block a release?

<!--
Questions 1, 2, 3, 4 and 7 of lecture section 9. Spend most of the hour on capstone reviews: put two participants' before/after summaries side by side and ask the group which review they would approve.
Common mistakes to surface (lecture section 11): tuning thresholds to the last run; keeping only the synthetic cases the agent passes; trusting aggregates over small categories; committing a baseline from a run with a flag on; red-team assertions on text only.
-->

---

<!-- _class: lead -->

# Close-Out: What You Take Home

Four days, one loop: **traces → error analysis → golden set → gate → deploy**, and back.

Take home: the golden set and its dataset card · deterministic trajectory metrics · a calibrated judge · a span tree per run · a harness with guards and one extension seam · a gate that checks its own evidence

<span class="small">Return to Day 1's question: *"Did last week's prompt change make tool selection better or worse, and by how much?"* You can now answer it with a number, its noise, and a gate.</span>

<!--
Close-out for the workshop. Return to the hands-up count from the Day 1 opening deck (who had an eval suite that would catch a tool-selection regression).
Ask each participant for one thing they will add to their own agent's pipeline next week, and which file in this repository they will copy it from.
Collect lab feedback through the repository's issue forms (.github/ISSUE_TEMPLATE). Remind everyone that live AWS spend stays opt-in: nothing in the repo creates resources or submits jobs without explicit consent.
-->
