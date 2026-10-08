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

## Phase 7 — README and DECISIONS `[x]`
- [x] `README.md`: install, mock vs live, AWS prerequisites (model access + Anthropic use-case form, region, optional KB, S3 bucket, OIDC role), listing model IDs, cost expectations (Nova priced; Claude = unknown until filled), CloudWatch GenAI observability env vars, troubleshooting, pinned versions (`make pins`), placeholder list (AWS account/bucket values)
- [x] `docs/DECISIONS.md` entries 1–35; `AGENTS.md` refreshed
- [x] Clean-clone verification with AWS vars unset: `make setup` (48 s) → `make test` (128 passed) → `make notebooks` (8 passed) → `make ci` (gate passed, 0 checker errors). `STOCKROOM_WEAKNESSES=ambiguous_tool_desc make ci` fails at the gate with the metrics table (tool selection 0.64, answer correctness 0.78, 4 golden Promptfoo cases failed, 8/8 red team held)
- [x] Phases committed on `main` (plus merge commits from the three worktree branches and small follow-ups)

## Session log
- 2026-10-08, PR #2 (`review-suggestions` → `main`): committed `suggestions.md` (TASKS and DECISIONS cite it). Two `/code-review` passes, both posted on the PR. Fixed: a blocked call resent unchanged now trips the repeated-call guard, and only schema-invalid calls count towards the invalid-calls stop; a run guard's wrong return type is a `TypeError`; arguments are validated once per call; config requires `TOKEN_BUDGET` > `MAX_TOKENS`; `--write-baseline` refuses without a manifest; category gates honour `max` and the cost rule catches a rise from zero; an empty suite reaches the gate's readable rejection; one `answer_value()` rule for the gate and the report; the answer interval counts the cases `aggregate()` counts; `extra` cannot replace gate-checked fields; the Day 3 `suite()` cache is keyed on live guard objects; the nightly report no longer compares against the mock baseline. Left as recorded trade-offs: blocked calls in `invalid_call_rate`, a halting guard's call not recorded (matches `RepeatedCallGuard`), the PR site build without a paths filter. `make ci` passed after each round (158 unit tests at the end; golden metrics unchanged).
- 2026-10-08, review follow-up (branch `review-suggestions`; DECISIONS 41–46): triaged `suggestions.md` and implemented the P0 items and most P1 items. **Gate:** evidence checks (coverage vs the manifest, dataset hash, finite metrics, every Promptfoo case present; a requested input that is missing fails), baseline provenance and per-case outcomes (an incompatible baseline is an explicit failure; `--write-baseline` refuses weakened or incomplete runs), a paired "cases that changed vs baseline" list, and two new rules — `category_gates` and a token-cost limit — because `naive_retry` and `oversized_payload` passed the old gate; `test_pr_gate_rejects_every_seeded_weakness()` now holds that property. **Statistics:** `stockroom.evals.stats` (run-to-run spread, case bootstrap, `required_max_drop()`, `floor_is_safe()`) replaces the pooled normal interval; `stockroom.evals.report.results_document()` builds `eval_results.json` everywhere. **Day 4:** four exercises on a required offline path (label validation before any agent run, a non-vacuous red-team case with a paraphrase, floor/noise/allowed-regression through the real gate, a capstone that makes the aggregate gate reject the regressions it missed and writes `reports/day4/`), optional AWS extensions, bounded AgentCore polling with every terminal status and spend consent; the lecture's threshold section, diagram direction, CI order and lab plan now match. **Day 3** (worktree agent): `Harness(run_guards=...)` seam, construction lab with `injection_unguarded` on, sanitizer/compaction limits pinned in tests, token budget reserves `MAX_TOKENS`, fix-log suites computed once. **Notebooks:** `stockroom.exercises` ids, checklist cells, strict mode for solutions in `make notebooks`. **Style** (worktree agent): `docs/STYLE_GUIDE.md`, canonical titles in `docs/workshop.yml`, `scripts/check_style.py` in `make ci` and CI, titles normalised, Days 1–2 numbering and 90-min morning lab, one Node statement, `pages.yml` builds on PRs. **Offline:** Promptfoo/Marp from `node_modules/.bin`. Golden metrics unchanged; baseline regenerated (provenance + per-case outcomes added). Verified on the merged branch: `make ci` end to end (150 unit tests, 51 golden, Promptfoo 18/18, gate passed, 4 student + 4 strict solution notebooks, both checkers 0 problems); `make site` rendered all pages on the style branch before the Day 3/4 merges (not re-run after).
- 2026-10-08, repository quality review: added `suggestions.md` with prioritized, source-grounded recommendations for later-day lab depth, Title Case and canonical terminology, dataset acceptance, threshold teaching, gate evidence/provenance, exercise completion, site QA, and instructor hand-off. Reviewed sources and generated notebook exercise counts; the lecture reference checker passes (8 documents, 0 errors/warnings). An in-memory gate probe accepted empty Promptfoo results and mismatched mode/dataset provenance when headline metrics matched the baseline; recommended explicit validation. Documentation-only review; no live AWS calls, deployment, or baseline changes.
- 2026-10-07, org profile follow-up: recovered the scratchpad README draft and opened https://github.com/genial-labs-ai/.github/pull/1 (commit `2563062`) to describe all three workshops. Qualified the score drop as a deterministic mock result and OIDC as live CI; clarified key-free NLP paths and the capstone exception; omitted exact tensors hours because the upstream README and deployed site disagree; removed the unsupported all-repos issue-template claim. Eight external site links return HTTP 200, both mirrors carry CONTRIBUTING.md and LICENSE, and `git diff --check` passes. PR remains unmerged; the public org profile is unchanged.
- 2026-10-07, session 5: website redesign (DECISIONS 40). New `site/_tokens.scss` (palette), `site/theme.scss` (rules, publishes the tokens as `--ws-*` custom properties for Mermaid) and `site/theme-dark.scss` (token overrides only), vendored Inter / Source Serif 4 in `site/fonts/`, `site/logo.svg` (navbar logo and favicon), landing page rebuilt (`page-layout: full`, hero band, facts strip, thesis, model/harness/environment cards, four day cards, the lecture-and-lab path, weakness cards, mock vs live, daily rhythm, outcomes, audience), navbar with Lectures/Notebooks/Slides menus and a Teach link, id'd sidebar so the landing page has none, footer links, `highlight-style: a11y`, OS colour-scheme on first visit, Mermaid palette per theme, `gfm.lua` splits "Day N — title" into title + eyebrow on lecture and notebook pages, `site/slides.scss` imports the same tokens and the deck loads the vendored fonts. A code review on PR #1 found seven issues (notebook titles not split, deck fonts not loaded, orphan eyebrow on narrow screens, inline formatting dropped from split titles, palette copied three times, dead rules, DECISIONS wording); all fixed before merge. A second round found seven more (the eyebrow's `@extend` beat its own narrow-screen rule, diagrams not redrawn on the theme toggle, scheme read from storage rather than the body class, the first-visit storage write, `pagetitle` overwritten on pre-titled pages, `lighten()`, inconsistent `!default`); also fixed. A third round found six more small ones (unchained Mermaid runs, drawing with an empty palette after the timeout, site-wide `overflow-x: clip`, Node 22 vs the README's ≥ 20, three copies of the label recipe, the first-visit snippet untracked as a follow-up); fixed, plus the lecture-agenda mismatch recorded under follow-ups. A fourth round: start the diagrams after Quarto's own DOMContentLoaded handler (no light flash on a dark visit), keep polling instead of drawing a stale palette, no storage write under `file:`, explicit dash comparison and a `pagetitle` guard in the filter, hero glow from the tokens, and the OFL licence texts vendored beside the fonts; the navbar menus duplicating the sidebar and the 20-row path grid were left as they are. Checked in light and dark and at phone width; `scripts/check_lecture_refs.py` 0 errors; `make site` rendered end to end.
- 2026-10-06/07, session 1: phases 1–7 completed; three worktree sub-agents produced notebooks, lectures, slides, instructor guide and the reference checker; all verified on a clean clone.
- 2026-10-07, session 4: GitHub Pages website with Quarto (DECISIONS 39). `_quarto.yml`, `index.qmd` landing page, `site/` (theme, slides theme, `filters/gfm.lua`), `notebooks/_metadata.yml` (execute in mock mode at build time), `slides/intro.qmd` kickoff deck (reveal.js, 14 slides, figures from the committed baseline and the session-1 regressed run), `.github/workflows/pages.yml` (uv + node + Quarto 1.6.40, deploys with `actions/deploy-pages`), `make site` / `make site-preview`, checker globs extended to `slides/*.qmd` and `index.qmd`, README website badge and row, `docs/social-preview.png` (1280×640, for the repo's social preview). Pages enabled with the Actions build type and the repo homepage set to the site URL.
- 2026-10-07, session 3: repository polish for the org. Added `LICENSE` (MIT, Genial Labs), `CITATION.cff` (validated with cffconvert), `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.editorconfig`; under `.github/`: `CODEOWNERS`, PR template with a metrics/baseline section, three issue forms (bug, lab feedback, proposal) plus contact links, `dependabot.yml` (monthly grouped PRs for uv, npm, Actions), `labeler.yml` + `workflows/labeler.yml` (actions/labeler v7), `release.yml` categories. README got a badge row, a Colab/solutions table for the four notebooks and a contributing/security section. On GitHub: 15 topics, 15 labels (matching labeler and release categories), private vulnerability reporting and Dependabot alerts/security updates enabled. DECISIONS 38.
- 2026-10-07, session 2: published to GitHub. Org `genial-labs-ai` (the requested `genial-labs` is taken on GitHub), public repo `system_agent_harness_aws`; placeholders replaced (DECISIONS 2); first hosted CI run exposed the `setup-uv@v10` tag bug, fixed by pinning `v10.2.0` (DECISIONS 36). Live workflow switched to manual dispatch only, no cron (DECISIONS 37).

## Follow-ups worth doing (not required by the brief)
- Website: when `.github/workflows/pages.yml` moves past Quarto 1.6.40, delete the first-visit colour-scheme snippet in `_quarto.yml` (it writes Quarto 1.6's internal `quarto-color-scheme` storage key) and set `respect-user-color-scheme: true` instead (review on PR #1).
- [x] 2026-10-08: Days 1–2 lecture agendas aligned with the guide's 90-minute morning lab (DECISIONS 41).
- [x] 2026-10-08: guard seam on `Harness` (`run_guards`, DECISIONS 45); `MaxToolPayloadGuard` stays a payload wrapper by design.
- From the 2026-10-08 review, not done (needs a decision or a dedicated session): Day 2–4 teaching decks with speaker notes (`suggestions.md` §11); a documented artefact hand-off from Days 1–3 into Day 4 beyond `reports/day4/` (§12); rendered-site browser checks per day in light/dark and desktop/mobile (§10 — the PR site build now catches failed renders, not layout); generating duplicated labels from `docs/workshop.yml` instead of validating copies (§2).
- [x] 2026-10-08: guard-blocked calls counted apart from invalid ones (`blocked_call_rate`, DECISIONS 47).
- `scripts/validate_data.py` keeps its own list of termination reasons; a new `TerminationReason` value would need adding there too.
- The live workflow reports without a baseline, since the committed one is mock and would never be comparable (DECISIONS 42). Once a live run exists, decide whether to commit a separate live baseline for its report.
- Add more bias-probe pairs to `data/judge_calibration` before trusting the probes on a live judge (DECISIONS 25).
- Exercise live mode end to end on a machine with a working `aws` CLI and record real nightly numbers.
- [x] 2026-10-07: `<GITHUB_ORG>/<GITHUB_REPO>` replaced with `genial-labs-ai/system_agent_harness_aws` across README, docs, lectures and notebook sources (DECISIONS 2).

## Known gaps / ideas (not required by the brief)
- Mock-mode scores are an upper bound (deterministic simulator); live nightly reports the real numbers with CIs.
- `aws` CLI is broken on the authoring machine, so live mode is documented and unit-tested with stubs but was not exercised end-to-end here.
- Phoenix is an optional extra (`uv sync --extra phoenix`); CI installs without it.
