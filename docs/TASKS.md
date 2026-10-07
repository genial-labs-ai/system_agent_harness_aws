# Build status and task list

Living checklist for the workshop repository build. Update it at the end of every working
session (it is the hand-off document between sessions and between sub-agents).
Plan of record: `~/.claude/plans/pasted-content-id-a1b1-project-precious-cosmos.md` (local to the
author's machine); the brief's phases are reproduced here.

Legend: `[x]` done and verified · `[~]` in progress · `[ ]` not started

## Phase 1 — Scaffold `[x]` (commit `7283455`)
- [x] uv project, `pyproject.toml` with verified pins, `uv.lock`, `.python-version` 3.12
- [x] `Makefile` (setup/lint/test/eval/promptfoo/notebooks/mcp-server/ci/…), `.devcontainer`
- [x] `src/stockroom/config.py` (env-driven, weakness flags, mode detection), `cost.py`, `pricing.yaml`
- [x] `scripts/fetch_pricing.py` (public Price List API), `scripts/print_pins.py`
- [x] `AGENTS.md`, `.gitignore`, `docs/DECISIONS.md` (phase-1 entries)
- Check passed: `uv sync`, `make lint`

## Phase 2 — Data `[x]` (commit `c6daa1b`)
- [x] `scripts/generate_seed_data.py` → `data/catalogue.json` (41 products), `orders.json` (25, 5 legacy), `suppliers.json`
- [x] `data/policy_docs/` six markdown docs; `restock_policy.md` §4 holds the seeded indirect injection
- [x] `data/golden/stockroom_golden_v1.jsonl` (50 cases, 12 categories), `mock_scripts/` (G035, G045, G046), `DATASET_CARD.md`, `manifest.json`
- [x] `data/judge_calibration/calibration_v1.jsonl` (32 items incl. bias probes) + README
- [x] `data/restock_rules.json`, `scripts/validate_data.py` (schemas, cross-refs, coverage, manifest)
- Check passed: `make validate-data`

## Phase 3 — Core code `[x]` (commit `18855ee`)
- [x] `agent/types.py`, `agent/retrieval.py` (BM25), `agent/tools.py` (5 tools, ledger, legacy shard, 3 weakness flags)
- [x] `agent/bedrock_adapter.py`: `BedrockConverseClient` (live), `FakeBedrockClient` (scripted turns + `HeuristicPlanner`)
- [x] `agent/harness.py`: state machine, schema interception, 4 guards, compaction, tool-output quarantine, `RunResult`
- [x] `evals/otel_tracer.py` (GenAI semconv spans, memory/console/phoenix/cloudwatch exporters, `detect_loops`)
- [x] `evals/judge.py` (FakeJudge v1/v2, BedrockJudge, DeepEval + RAGAS bridges), `evals/rubrics/*.md`
- [x] `evals/metrics.py` (deterministic metrics, `ToolCallEvaluator`, `OutputEvaluator`, `evaluate_case`, `aggregate`)
- [x] `evals/calibration.py` (agreement, kappa, confusion, 3 bias probes), `evals/golden.py`
- [x] `mock_server/mcp_inventory_server.py` (mcp 2.x `MCPServer`, stdio + streamable-http), `agent/mcp_client.py`, `scripts/mcp_smoke.py`, `cli.py`
- [x] Tests: config, tools, harness guards, seeded weaknesses, judge/calibration, tracer, MCP (in-memory + stdio), metrics — 65 passing
- Check passed: `make test-unit`; every weakness flag produces its expected failing metric
  (ambiguous desc → tool selection 1.0→0.64; oversized → TOKEN_BUDGET; naive retry → MAX_STEPS + loop; injection → forbidden call + prompt leak)

## Phase 4 — Eval suite and CI `[~]`
- [x] `eval_thresholds.yaml` (single source of thresholds)
- [x] `tests/test_trajectory_regression.py` (DeepEval test cases over the golden set → `reports/eval_results.json`, repeats + CIs)
- [x] `scripts/check_thresholds.py` (gates, baseline diff, red-team gate, Markdown summary, `--write-baseline`)
- [x] `promptfooconfig.yaml` + `scripts/promptfoo_provider.py` + `scripts/promptfoo_asserts.py` (10 golden + 8 red-team cases, offline)
- [x] `.github/workflows/agent_eval_ci.yml`, `agent_eval_nightly.yml` (OIDC), `docs/aws/iam_policy.json`, `oidc_trust_policy.json`, `docs/aws/README.md`
- [x] `scripts/sync_datasets_s3.py` (versioned golden/report sync, model check)
- [ ] Verify `make eval` (first run in progress) and `make promptfoo` (first npx run in progress); fix anything that surfaces
- [ ] Verify the eval suite over `STOCKROOM_TOOL_TRANSPORT=mcp-http` with `make mcp-server` running (CI uses this path)
- [ ] `make baseline` → commit `reports/baseline/main.json`
- [ ] `make ci` passes; `STOCKROOM_WEAKNESSES=ambiguous_tool_desc make ci` fails at the gate with a readable summary
- [ ] Fix 3 lint lines (`scripts/check_thresholds.py`, `scripts/sync_datasets_s3.py`) that slipped past the phase-3 commit
- [ ] Unit tests for `check_thresholds.py` (gate pass/fail, baseline regression, red-team split) and the promptfoo asserts
- [ ] `tests/test_live_adapters.py`: stubbed boto3 clients for `BedrockConverseClient`, `BedrockJudge`, `sync_datasets_s3` (no network)
- [ ] DECISIONS entries for phase 3/4 (heuristic planner, pytest vs `deepeval test run`, RAGAS custom LLM, hand-written red team, no agent job type in Bedrock Evaluations, MCP v2, semconv status)

## Phase 5 — Notebooks `[ ]`
- [ ] `scripts/build_notebooks.py` (jupytext percent sources → student + `solutions/` .ipynb; tags `exercise` / `check` / `solution`)
- [ ] `notebooks/src/day1_deterministic_and_rag_evals.py` — taxonomy demo on the weakness flags, golden-set tour, DeepEval assertions, RAGAS (BM25 local; KB branch live); 3–5 exercises
- [ ] `notebooks/src/day2_judge_calibration_and_otel_traces.py` — spans (memory + optional Phoenix), `ToolCallEvaluator`, calibration v1→v2, bias probes; exercises
- [ ] `notebooks/src/day3_building_custom_agent_harness.py` — guards/validation/compaction, MCP server via client, toggle each weakness and chart metric movement; exercises
- [ ] `notebooks/src/day4_bedrock_evaluations_and_ci_gating.py` — synthetic case generation + judge filter, Promptfoo red team, thresholds from baseline variance, `check_thresholds.py` on regressed vs fixed, Bedrock Evaluations job builder (payload only; submit behind `STOCKROOM_CONFIRM_AWS_SPEND=1`), AgentCore `evaluate()` script (live only); exercises
- [ ] Setup cell pattern: `%pip install` guarded by `find_spec("stockroom")`, `REPO_URL` placeholder, `detect_mode()` banner
- [ ] `make notebooks` executes all 8 notebooks in mock mode (nbmake)

## Phase 6 — Teaching materials `[ ]`
- [ ] `lectures/day1_llm_eval_foundations.md` … `day4_aws_ci_cd_redteaming.md` (objectives, timed agenda, Mermaid, notebook link, discussion questions, common mistakes)
- [ ] `slides/DAY1_MOTIVATIONAL_SLIDES.md` (Marp, 15–20 slides, speaker notes, composite post-mortem labelled hypothetical, no invented numbers); `make slides` renders
- [ ] `docs/INSTRUCTOR_GUIDE.md` (timings, setup checklist, common participant failures)
- [ ] `scripts/check_lecture_refs.py` — every `src/...py` path and `symbol()` reference in lectures/slides resolves (AST)
- [ ] Attribute error-analysis practice to Hamel Husain / Shreya Shankar with links; real incidents only with sources

## Phase 7 — README and DECISIONS `[ ]`
- [ ] `README.md`: install, mock vs live, AWS prerequisites (model access + Anthropic use-case form, region, optional KB, S3 bucket, OIDC role), listing model IDs, cost expectations (Nova priced; Claude = unknown until filled), CloudWatch GenAI observability env vars, troubleshooting, pinned versions (`make pins`), placeholder list (`<GITHUB_ORG>/<GITHUB_REPO>`)
- [ ] `docs/DECISIONS.md` complete; `AGENTS.md` refreshed
- [ ] Clean-clone verification in a scratch dir with AWS vars unset: `make setup && make test && make notebooks && make ci`
- [ ] Final commit; `git log` shows one commit per phase

## Known gaps / ideas (not required by the brief)
- Mock-mode scores are an upper bound (deterministic simulator); live nightly reports the real numbers with CIs.
- `aws` CLI is broken on the authoring machine, so live mode is documented and unit-tested with stubs but was not exercised end-to-end here.
- Phoenix is an optional extra (`uv sync --extra phoenix`); CI installs without it.
