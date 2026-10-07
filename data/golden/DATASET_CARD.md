# Dataset card — Stockroom golden set v1

| | |
|---|---|
| **File** | `data/golden/stockroom_golden_v1.jsonl` (one JSON object per line) |
| **Version** | 1.0.0 (see `manifest.json` for the content hash) |
| **Size** | 50 cases |
| **Domain** | Stockroom: a fictional inventory and order-support agent with five tools |
| **Licence** | Same as the repository |

## Purpose
Regression testing of the Stockroom agent's *behaviour*, not only its final answers: which tools it
calls, with which arguments, in which order, how it terminates, and whether its answer contains the
facts the tools returned. The set is parametrised into `tests/test_trajectory_regression.py` and the
Promptfoo suite; thresholds live in `eval_thresholds.yaml`.

## Sourcing and labelling
Every case was written by the workshop authors against `data/catalogue.json`, `data/orders.json` and
`data/policy_docs/`. Expected tool calls, arguments, facts and termination reasons were derived by
reading the data files, not by running the agent, so the set does not simply replay whatever the
agent happened to do (that would be label leakage). Reference answers are written by hand.

## Schema
| field | meaning |
|---|---|
| `id` | stable identifier `G###`; never reuse an ID after deleting a case |
| `category` | one of `product_search, stock_lookup, order_status, restock_request, policy_rag, multi_step, out_of_scope, injection, transient_tool_error, schema_drift, ambiguous_query, context_bloat` |
| `query` | the user turn |
| `expected_tools` | ordered list of `{name, args}`; `args` is the minimal correct argument set |
| `trajectory_match_mode` | `exact` (same calls, same order, nothing else), `in_order_subset` (expected calls appear in order; extra calls allowed), `any_order` (same multiset) |
| `expected_facts` | substrings that must appear in the final answer (case-insensitive, numbers normalised) |
| `forbidden_facts` | substrings that must *not* appear (hallucinations, leaked prompts) |
| `expected_termination` | `COMPLETED`, or a guard reason such as `MAX_STEPS` |
| `must_not_call` | tools that must not be executed at all |
| `mock_script` | optional scripted model turns in `mock_scripts/` for reproducible trajectories |
| `reference_answer` | human reference used by the judge metrics and by Bedrock Evaluations (`referenceResponse`) |
| `retrieval` | for policy cases: `reference_contexts` (`file#section`) and a `reference` sentence for RAGAS |
| `max_steps` | optional upper bound on harness steps (latency/cost proxy) |
| `tags`, `notes` | free-form |

## Coverage
Each tool appears in at least three cases, and each failure category from the Day 1 taxonomy
(wrong tool, wrong arguments, schema drift, loops/retries, context growth, injection, refusal,
hallucination) has at least two cases. `scripts/validate_data.py` enforces this.

## Known limitations and contamination risks
- The mock model is a deterministic simulator, so mock-mode scores are *upper bounds* on what a real
  model achieves; live nightly runs report the real numbers with confidence intervals.
- Queries are short, single-turn English. Multi-turn conversations are out of scope for v1.
- Because the set is public in the repo, do not use it to fine-tune or prompt-tune the agent; keep a
  private hold-out if you adapt the workshop for production.

## Versioning
Bump `version` in `manifest.json` and add a changelog line here whenever a case changes. The nightly
workflow uploads the set to `s3://<S3_BUCKET>/<S3_PREFIX>/golden/<version>/` via
`scripts/sync_datasets_s3.py`, so historical runs can be reproduced against the exact data they used.

### Changelog
- 1.0.0 (2026-10-06): initial 50 cases.
