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

## Phase 4 — Eval suite and CI `[x]`
- [x] `eval_thresholds.yaml` (single source of thresholds)
- [x] `tests/test_trajectory_regression.py` (DeepEval test cases over the golden set → `reports/eval_results.json`, repeats + CIs)
- [x] `scripts/check_thresholds.py` (gates, baseline diff, red-team gate, Markdown summary, `--write-baseline`)
- [x] `promptfooconfig.yaml` + `scripts/promptfoo_provider.py` + `scripts/promptfoo_asserts.py` (10 golden + 8 red-team cases, offline)
- [x] `.github/workflows/agent_eval_ci.yml`, `agent_eval_nightly.yml` (OIDC), `docs/aws/iam_policy.json`, `oidc_trust_policy.json`, `docs/aws/README.md`
- [x] `scripts/sync_datasets_s3.py` (versioned golden/report sync, model check)
- [x] `make eval` verified (50/50, writes `reports/eval_results.json` with CIs and DeepEval pass rates)
- [x] `make promptfoo` verified: 18/18 offline (10 golden + 8 red team); Node tooling now pinned in `package.json`/`package-lock.json`, installed by `make setup` and CI (`npm ci`)
- [x] Eval suite verified over `STOCKROOM_TOOL_TRANSPORT=mcp-http` with the server in the background (50/50)
- [x] `make baseline` → `reports/baseline/main.json` committed (sha 18855ee data 1.0.0)
- [x] Gate verified step by step: clean run passes; `STOCKROOM_WEAKNESSES=ambiguous_tool_desc` run fails (tool selection 0.64, answer 0.78, 4 Promptfoo failures) with the Markdown summary
- [x] Full `make ci` end to end passes (lint → data → unit → MCP server → eval over mcp-http → Promptfoo → thresholds → notebooks → slides → lecture checker)
- [x] Lint clean again (`make lint`)
- [x] `tests/test_check_thresholds.py` (gate pass/fail, baseline regression, red-team split, CLI, promptfoo asserts)
- [x] `tests/test_live_adapters.py`: stubbed boto3 clients for `BedrockConverseClient`, `BedrockJudge`, DeepEval live bridge, `sync_datasets_s3` dry run
- [x] DECISIONS entries 8–22 for phases 3/4

## Phase 5 — Notebooks `[x]` (merged branch commit `fb73972`; `make notebooks` = 8 passed in 71 s on main)
- [x] `scripts/build_notebooks.py` (jupytext percent sources → student + `solutions/` .ipynb; tags `exercise` / `check` / `solution`)
- [x] `notebooks/src/day1_deterministic_and_rag_evals.py` — taxonomy demo on the weakness flags, golden-set tour, DeepEval assertions, RAGAS (BM25 local; KB branch live); 3–5 exercises
- [x] `notebooks/src/day2_judge_calibration_and_otel_traces.py` — spans (memory + optional Phoenix), `ToolCallEvaluator`, calibration v1→v2, bias probes; exercises
- [x] `notebooks/src/day3_building_custom_agent_harness.py` — guards/validation/compaction, MCP server via client, toggle each weakness and chart metric movement; exercises
- [x] `notebooks/src/day4_bedrock_evaluations_and_ci_gating.py` — synthetic case generation + judge filter, Promptfoo red team, thresholds from baseline variance, `check_thresholds.py` on regressed vs fixed, Bedrock Evaluations job builder (payload only; submit behind `STOCKROOM_CONFIRM_AWS_SPEND=1`), AgentCore `evaluate()` script (live only); exercises
- [x] Setup cell pattern: `%pip install` guarded by `find_spec("stockroom")`, `REPO_URL` placeholder, `detect_mode()` banner
- [x] `make notebooks` executes all 8 notebooks in mock mode (nbmake)

## Phase 6 — Teaching materials `[x]` (merged branches `e474a8f`, `93dbfc8`)
- [x] `lectures/day1_llm_eval_foundations.md`, `lectures/day2_llm_as_a_judge_and_otel.md` (merged from worktree branch, commit `e474a8f`)
- [x] `lectures/day3_agent_harness_and_mcp_mocking.md`, `lectures/day4_aws_ci_cd_redteaming.md` (merged, commit `93dbfc8`)
- [x] `slides/DAY1_MOTIVATIONAL_SLIDES.md` (19 slides with notes; `make slides` renders 19 sections)
- [x] `docs/INSTRUCTOR_GUIDE.md`
- [x] `scripts/check_lecture_refs.py` — 0 errors over 6 documents; called by `make ci` and the CI workflow (notebook paths warn until built)
- [x] Error-analysis practice attributed (Husain blog, Shankar et al. arXiv 2404.12272, both fetched); no real incidents used anywhere

## Phase 7 — README and DECISIONS `[~]`
- [x] (draft, commit `246a5d6`) `README.md`: install, mock vs live, AWS prerequisites (model access + Anthropic use-case form, region, optional KB, S3 bucket, OIDC role), listing model IDs, cost expectations (Nova priced; Claude = unknown until filled), CloudWatch GenAI observability env vars, troubleshooting, pinned versions (`make pins`), placeholder list (`<GITHUB_ORG>/<GITHUB_REPO>`)
- [x] `docs/DECISIONS.md` entries 1–35; `AGENTS.md` refreshed
- [~] Clean-clone verification in a scratch dir with AWS vars unset: `make setup && make test && make notebooks && make ci` (setup/lint/test passed on the first clone; full run in progress)
- [ ] Final commit; `git log` shows one commit per phase

## Known gaps / ideas (not required by the brief)
- Mock-mode scores are an upper bound (deterministic simulator); live nightly reports the real numbers with CIs.
- `aws` CLI is broken on the authoring machine, so live mode is documented and unit-tested with stubs but was not exercised end-to-end here.
- Phoenix is an optional extra (`uv sync --extra phoenix`); CI installs without it.
