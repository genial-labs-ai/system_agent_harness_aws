---
marp: true
theme: gaia
paginate: true
size: 16:9
title: Why Your AI Agent Fails in Production (and How Evals Fix It)
description: Day 1 opening deck for the workshop "Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD"
style: |
  section { font-size: 26px; }
  section.lead h1 { font-size: 56px; }
  table { font-size: 22px; }
  code { font-size: 0.9em; }
  .small { font-size: 20px; color: #555; }
  .hypo { background: #fff4e5; border-left: 6px solid #e08a00; padding: 6px 12px; font-size: 22px; }
---

<!-- _class: lead -->

# Why Your AI Agent Fails in Production (and How Evals Fix It)

**Evaluating Autonomous Agents: Systems, Harnesses & AWS Production CI/CD**
Day 1 — opening session

Running example: **Stockroom**, an inventory and order-support agent with five tools

<!--
Welcome. This deck is the motivation for the four days; the lecture notes in lectures/day1_llm_eval_foundations.md carry the detail.
Ground rule for everything you will see today: every number on these slides was produced by the repository you cloned, in mock mode, and you can reproduce it with `make test-unit` or a notebook cell. There are no industry statistics, no named companies, no real incidents. Where we show a post-mortem it is a labelled composite built from the repo's own seeded weakness.
Ask the room: who has an agent in production? Who has an eval suite that would catch a tool-selection regression? Keep the hands-up count for the last slide.
-->

---

## The question you cannot answer today

> "Did last week's prompt change make tool selection better or worse, and by how much?"

Most teams answer with one of:

- "It felt better on the three prompts I tried."
- "Nobody complained."
- "We re-ran the demo."

By Friday the answer is a number, a confidence interval and a CI gate — from
`reports/eval_results.json` and `scripts/check_thresholds.py`.

<!--
This is the diagnostic for vibe-driven development. If the answer is a feeling, you are shipping on vibes.
It is not a criticism; it is where everyone starts. The point of the workshop is that the alternative is cheaper than it looks, because most of the eval suite is deterministic and runs offline.
-->

---

## From vibe-driven to engineering rigor

| Vibe-driven | Measured |
|---|---|
| Three remembered prompts | A golden set with expected **behaviour** (tools, args, order, termination) |
| "Looks right" | Deterministic metrics + a judge calibrated against humans |
| Ship and wait for complaints | A PR gate with thresholds from a baseline |
| Debug from the chat log | A span tree per run, in a viewer |
| Cost found on the invoice | Token budget + step limit + cost estimate per run |

Every row on the right is a file in the repo you cloned.

<!--
Left column is not a strawman; it's how most of us started. Right column maps to: data/golden, src/stockroom/evals/metrics.py and judge.py, eval_thresholds.yaml with scripts/check_thresholds.py, src/stockroom/evals/otel_tracer.py, and the guards in src/stockroom/agent/harness.py.
The transition is incremental: the deterministic metrics alone (day 1) already catch the failure on slide 10.
-->

---

## Agent = Model + Harness (+ Environment)

```text
PLAN → CALL_MODEL → EXECUTE_TOOLS → OBSERVE → CALL_MODEL … → DONE | FAILED
                 ↑ guards: max_steps · token_budget · repeated_call · wall_clock
                 ↑ schema interception · compaction · tool-output quarantine
```

- A **single-prompt eval** scores one completion.
- An **agent** is a loop owned by *your* code, calling tools owned by *someone else's* code.
- The failures that reach production live in the loop and in the tools, not only in the model.

The unit under test is the **run** (`RunResult`), not the completion.

<!--
This is the thesis of the whole workshop. The state machine is Harness.run() in src/stockroom/agent/harness.py. Each guard is a class you can unit test.
Make the point that "the model was fine" is a common and often true post-mortem conclusion: the model asked for a retry, the harness let it retry forever.
-->

---

<!-- _class: lead -->

# Anatomy of a runaway agent

<div class="hypo"><b>Composite, hypothetical post-mortem.</b> Built from the repository's own <code>naive_retry</code> weakness flag and golden case G043. Not a real incident, not a real company. Every number is produced by <code>tests/test_seeded_weaknesses.py</code> in mock mode.</div>

<!--
Say the label out loud. This is a teaching construct. The reason to do it as a post-mortem is that the shape is what you will recognise when it happens to you: a benign backend blip, a model that does the locally reasonable thing, a harness with one missing guard.
-->

---

## Timeline (hypothetical)

| Step | What happened |
|---|---|
| 1 | User asks: *"What's the status of order ORD-9001?"* |
| 2 | Model calls `get_order_status(order_id="ORD-9001")` — correct tool, correct argument |
| 3 | The legacy order shard (`ORD-9xxx`) times out: `transient` error returned to the model |
| 4 | Model re-issues the **identical** call. Shard still down. |
| 5–8 | Same call, same error, four more times |
| 9 | `MaxStepsGuard` stops the run at `MAX_STEPS=8`: termination `MAX_STEPS` |
| — | User gets: *"I had to stop before finishing (MAX_STEPS)…"* |

The model was never wrong. It picked the right tool with the right argument eight times.

<!--
Walk the timeline. The important observation is at the bottom: an answer-only or tool-selection-only eval scores every one of these calls as correct. The defect is that nothing in the loop recognised "same call, same error, again".
In the repo: StockroomTools.get_order_status() has an internal second attempt when the flag is off; with naive_retry on, the tool has no retry and the LegacyShardSimulator fails every attempt, and the HeuristicPlanner re-issues the call because that is what a model that sees "retry later" does.
-->

---

## The numbers the repo produces (mock mode, G043, `MAX_STEPS=8`)

| | `naive_retry` on | fixed harness |
|---|---|---|
| termination | `MAX_STEPS` | `COMPLETED` |
| model calls | **8** | 2 |
| tool calls executed | **8 identical** | 1 |
| input tokens (mock `chars/4` estimate) | **≈ 9.4k** | ≈ 1.9k |
| `ToolCallEvaluator` loops / repeated | 1 / 1 | 0 / 0 |
| answer | guard message | shipped, tracking PA77120931 |

Reproduce: `test_naive_retry_loops_until_max_steps()` in `tests/test_seeded_weaknesses.py`.

<!--
Roughly five times the input tokens for zero useful output, and that is with MAX_STEPS=8. Ask the room what their harness's step limit is. Many do not have one; then the number is bounded by the model's context window and your patience.
The token numbers are the fake client's chars/4 estimate, not a tokenizer; the ratio is what matters.
-->

---

## What it costs: the formula, not a number

```text
cost = input_tokens / 1000 × price_in_per_1k  +  output_tokens / 1000 × price_out_per_1k
```

- The agent prints this after every live run (`RunResult.print_cost_summary()`).
- Prices come from `pricing.yaml`. For third-party models they are **`null` = unknown** until you fill them in or set `AGENT_PRICE_INPUT_PER_1K` / `AGENT_PRICE_OUTPUT_PER_1K`. The harness never guesses.

**Illustrative only** — if the agent were Amazon Nova Pro at the published price in `pricing.yaml` ($0.0008 / 1K input, $0.0032 / 1K output, verified 2026-10-06):

| run | tokens in / out | ≈ cost per run |
|---|---|---|
| runaway (8 identical calls) | 9,468 / 48 | ≈ $0.0077 |
| healthy | 1,882 / 80 | ≈ $0.0018 |

Multiply the difference by your request volume and by how long the shard stays down.

<!--
Do not let anyone leave with a dollar figure in their head as "the cost of the incident". The point is the shape: a ×4–5 per-request cost for zero value, with no ceiling except MAX_STEPS. With a bigger model, a bigger context, or no step limit, the multiplier grows.
The Nova Pro numbers are the ones scripts/fetch_pricing.py pulled from the public Price List API on 2026-10-06; Claude prices are not in that feed and are null in the file.
-->

---

## What would have caught it

| Layer | Fix in the repo | Eval that proves it |
|---|---|---|
| Harness | `RepeatedCallGuard` (window 3) → `REPEATED_CALL` termination | `loop_rate` in `aggregate()`; `ToolCallEvaluator.from_run()` |
| Tool | second attempt inside `get_order_status()` | G043 completes in 2 model calls |
| Gate | `loop_rate ≤ 0.10`, `termination_match_rate ≥ 0.90` in `eval_thresholds.yaml` | `scripts/check_thresholds.py` fails the PR |
| Traces | `execute_tool get_order_status` × 8 with `error.type=transient` | `detect_loops()` over spans (Day 2) |

Three of the four fixes have nothing to do with the prompt.

<!--
This slide is the argument for evaluating the harness. The model-side fix (a better prompt saying "don't retry more than twice") is the weakest of the four because it depends on the model obeying it every time.
-->

---

## Where failures live: model, harness, environment

| Layer | Stockroom example | Where to see it |
|---|---|---|
| **Model** | `quantity: "fifty"` instead of `50` — a schema violation the model emits | Golden case **G045**, scripted in `data/golden/mock_scripts/G045.json`; intercepted by `validate_arguments()` |
| **Harness** | No repeated-call guard → 8-call loop | `naive_retry` flag (previous slides) |
| **Environment** | Ambiguous tool description; oversized tool payload; injected instruction in a policy doc | `ambiguous_tool_desc`, `oversized_payload`, `injection_unguarded` flags in `src/stockroom/config.py` |

The flags are feature flags (`STOCKROOM_WEAKNESSES=...`). Shipped default: all fixed, CI green.

<!--
Three layers, one flag or case each. Day 3 is where participants switch each flag on and watch which metric moves. Today just establish that each layer has its own failure shape and its own metric.
-->

---

## Model failure: schema drift (G045)

Query: *"Raise a restock request for fifty units of SKU-1020."*

1. Model: `create_restock_request(sku="SKU-1020", quantity="fifty")`
2. Harness: `validate_arguments()` → `quantity: 'fifty' is not of type 'integer'` — **never reaches the tool**, structured error + expected schema returned
3. Model: `create_restock_request(sku="SKU-1020", quantity=50)` → `RSR-0001` created
4. Run completes in 3 model calls; the invalid call is recorded, counted (`invalid_calls`), not executed

Without interception: a `TypeError` inside the tool, no schema in the error, no self-correction.

<!--
This is the model being wrong and the harness making it recoverable. The metric that sees it is invalid_call_rate; the golden case allows the recovery (in_order_subset, max_steps 3).
-->

---

## Environment failure: the ambiguous tool description

`search_products` description with the flag on: *"Look up products, inventory, stock level, units in stock for a SKU, orders, order status and tracking by order id, and anything else about the warehouse."*

| metric (50 cases, mock) | default | `ambiguous_tool_desc` |
|---|---|---|
| `tool_selection_accuracy` | 1.00 | **0.64** |
| `order_status` tool selection | 1.00 | **0.00** |
| `answer_correctness` | 1.00 | 0.78 |

Answers stay *mostly right* (search results include stock levels). The agent is broken anyway.

<!--
test_ambiguous_tool_description_drops_tool_selection() asserts exactly this: answer_correctness > tool_selection_accuracy. This is the single most convincing slide for "evaluate trajectories, not answers". Nobody changed the model. Someone edited a docstring.
The other two environment flags: oversized_payload takes G049 from 3.4k input tokens to 14k and a TOKEN_BUDGET termination; injection_unguarded makes the agent follow a maintenance note in restock_policy.md section 4 and create a 10,000-unit request.
-->

---

## Myth 1: "If the final answer is right, the agent is right"

**What you will measure (Day 1):** under `ambiguous_tool_desc`, answer correctness 0.78 while tool selection falls to 0.64 and `order_status` tool selection is 0.00.

**Why it matters:** the wrong tool is a latent bug — it works until the search index lags the stock table, and then every "right" answer is stale.

Metrics: `tool_selection_score()`, `argument_correctness_score()`, `trajectory_matches()` in `src/stockroom/evals/metrics.py`.

<!--
Rebut with the table two slides back. Mention that the golden set records expected tools and arguments per case precisely so this can be measured without a model.
-->

---

## Myth 2: "An LLM judge is as good as a human label"

**What you will measure (Day 2):** the same fake judge on the same 24 human-labelled items, two rubric versions.

| | rubric v1 | rubric v2 |
|---|---|---|
| agreement | 58% | 92% |
| Cohen's kappa | **0.25** | **0.83** |
| false passes | 10 | 2 |

Plus three bias probes: position, verbosity, self-preference (`src/stockroom/evals/calibration.py`).

**A judge is a classifier of the human label.** Validate it before it gates anything.

<!--
Kappa 0.25 is barely above chance even though "58%" sounds half-right: v1 passes everything. The two remaining v2 disagreements are items that are string-correct but misleading, which no fact matcher catches; that's what a real cross-family judge is for in live mode.
Attribution for the practice comes on Day 2: Hamel Husain's "Your AI Product Needs Evals" and Shankar et al., "Who Validates the Validators?".
-->

---

## Myth 3: "Agents are non-deterministic, so you can't gate on them"

**What you will measure (Days 1 and 4):**

- Deterministic trajectory metrics with **zero variance** in mock mode → a fixed 0.85 gate.
- Live runs with `STOCKROOM_EVAL_REPEATS=n` → mean and 95% CI per metric in `eval_results.json`.
- Thresholds set from baseline variance, not picked; regression gate at −0.05 vs `reports/baseline/main.json`.
- Hard invariants that are never allowed to flake: `must_not_call_ok_rate = 1.0`, red-team failures = 0.

Non-determinism is a reason to measure variance, not a reason to skip the gate.

<!--
tests/test_trajectory_regression.py computes the confidence intervals; scripts/check_thresholds.py applies eval_thresholds.yaml. Day 4 shows how to set the live thresholds from the nightly variance.
-->

---

## The eval flywheel

```text
   traces ──► error analysis ──► golden dataset ──► CI gate ──► deploy
     ▲                                                             │
     └─────────────────────────────────────────────────────────────┘
```

| Stage | In the repo |
|---|---|
| traces | `invoke_agent` / `chat` / `execute_tool` spans (`src/stockroom/evals/otel_tracer.py`), Phoenix locally, CloudWatch live |
| error analysis | read runs, cluster failures, label → `data/judge_calibration/` |
| golden dataset | `data/golden/` + `DATASET_CARD.md` + versioned in S3 (`scripts/sync_datasets_s3.py`) |
| CI gate | `eval_thresholds.yaml` → `scripts/check_thresholds.py` → PR comment |
| deploy | nightly live run with CIs; new failures become new cases |

<!--
The flywheel is the course structure. Each day adds one stage and connects it to the previous one. Emphasise that the loop closes: a production failure seen in a trace becomes a golden case, which becomes a gate.
-->

---

## The four days

| Day | Theme | You build |
|---|---|---|
| **1** | Foundations: taxonomy, golden sets, deterministic metrics, DeepEval, RAGAS | A versioned golden set and a metric table you trust |
| **2** | LLM-as-a-judge, calibration and bias probes; OpenTelemetry traces, Phoenix, CloudWatch | A calibrated judge and a span tree per run |
| **3** | Building the harness: guards, schema interception, compaction, injection quarantine; MCP server and client | A hardened harness and an MCP tool server |
| **4** | AWS CI/CD: synthetic cases, Promptfoo red team, thresholds from variance, Bedrock Evaluations, GitHub OIDC | A PR gate that fails on a regressed weakness |

Everything runs offline in mock mode; live AWS is opt-in (`STOCKROOM_MODE=live`).

<!--
Point out the offline-first rule: no credentials needed for any of the four labs. Live mode needs model access, two model IDs, and optionally an S3 bucket and a Knowledge Base; the README lists the prerequisites and the cost expectations.
-->

---

## What you will have built by Friday

- **Harness** — explicit state machine, four guards, schema interception, compaction, tool-output quarantine
- **Eval suite** — 50-case golden set, deterministic trajectory metrics, DeepEval and RAGAS bridges, no OpenAI key anywhere
- **MCP server** — the five Stockroom tools over stdio and streamable HTTP, driven by the same harness
- **Calibrated judge** — versioned rubrics, kappa and confusion against human labels, bias probes, cross-family (Claude agent / Nova judge) by default
- **CI gate** — thresholds in one file, baseline regression check, red-team gate, nightly live run with confidence intervals via GitHub OIDC

And the answer to the question on slide 2.

<!--
Return to the hands-up count from the first slide. The commitment: every participant leaves able to answer "better or worse, by how much" for their own agent, using this repo as a template.
-->

---

<!-- _class: lead -->

# Let's measure something

Open `notebooks/Day1_Deterministic_and_RAG_Evals.ipynb`
and run the first cell: it should say **MOCK mode**.

<span class="small">Lecture notes: `lectures/day1_llm_eval_foundations.md` · Ground rules for contributors: `AGENTS.md`</span>

<!--
Transition to the lab. First cell is detect_mode(); if anyone sees LIVE, they have credentials and model IDs in their environment and should decide deliberately whether they want to spend.
-->
