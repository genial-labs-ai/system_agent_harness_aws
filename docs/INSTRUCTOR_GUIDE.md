# Instructor Guide

*Evaluating Autonomous Agents*: four days, one running example. Every lab runs offline in mock
mode; live AWS is opt-in. This guide covers timings, setup, the failures participants actually
hit, facilitation notes per exercise, and how to regenerate the generated artefacts. Lecture
content is in `lectures/`; the decks are in `slides/`.

---

## 1. Daily timetable (09:00–17:00)

The same skeleton every day: ~1.5 h lecture, ~4 h lab in two parts, ~1 h review, 1 h 15 of breaks.

| Clock | Block | Minutes |
|---|---|---|
| 09:00–09:15 | Recap / warm-up (Day 1: introductions and setup check) | 15 |
| 09:15–10:45 | Lecture | 90 |
| 10:45–11:00 | Break | 15 |
| 11:00–12:30 | Lab part 1 | 90 |
| 12:30–13:15 | Lunch | 45 |
| 13:15–15:45 | Lab part 2 | 150 |
| 15:45–16:00 | Break | 15 |
| 16:00–17:00 | Review and discussion | 60 |

Per-day content of the three blocks:

| Day | Lecture | Lab part 1 (11:00) | Lab part 2 (13:15) | Review (16:00) |
|---|---|---|---|---|
| 1 — LLM Evaluation Foundations | failure taxonomy on the weakness flags, golden-set design, deterministic vs judge metrics, RAG metrics | golden-set tour, DeepEval assertions on `RunResult` | RAGAS over the BM25 policy index (Knowledge Base branch live only); write 3 new golden cases | dataset card review; what the taxonomy misses |
| 2 — LLM-as-a-Judge, Calibration, and OTel | LLM-as-a-judge pitfalls, calibration (agreement, kappa), bias probes, OpenTelemetry GenAI spans | spans in memory and (optional) Phoenix; `ToolCallEvaluator` from spans | calibration v1 → v2 on `data/judge_calibration/calibration_v1.jsonl`; position/verbosity/self-preference probes | which rubric change moved agreement and why |
| 3 — Agent Harness and MCP Mocking | `lectures/day3_agent_harness_and_mcp_mocking.md` | construction lab: a run guard through `Harness(run_guards=...)` with `injection_unguarded` on; payload wrapper; what validation, compaction and quarantine do not guarantee | MCP server via client; toggle each weakness and chart metric movement; fix one at a time | metric deltas per fix; AgentCore contrast |
| 4 — AWS CI/CD and Red Teaming | `lectures/day4_aws_ci_cd_redteaming.md` | synthetic cases validated against the data; a red-team case that holds across paraphrases | floor, noise and allowed regression; capstone: make the gate reject the regressions it misses; optional Bedrock Evaluations payload and AgentCore `evaluate` (live) | capstone reviews and before/after summaries side by side; take-home checklist |

Timing notes:

- `make ci` takes several minutes locally (notebooks execute). On Day 4 start the first `make ci`
  *before* lunch so the failing run is ready at 13:15.
- `make notebooks` executes all notebooks with a 900 s per-notebook timeout; on slow laptops run
  only the day's notebook with `uv run pytest --nbmake notebooks/<name>.ipynb`.
- Keep 10 minutes at the end of each lab part for participants to save their `reports/` outputs
  under a new name; Day 4 reuses Day 3 results.

---

## 2. Setup checklist (send one week before; verify at 09:00 on Day 1)

| Item | Requirement | Check |
|---|---|---|
| Python | 3.12 (pinned in `.python-version`; `pyproject.toml` requires `>=3.12,<3.13`) | `uv python install 3.12` is run by `make setup` |
| uv | installed and on `PATH` | `uv --version` |
| Node | Node 22 (the version CI uses; 20 or newer works) for Promptfoo 0.124.0 and Marp CLI 4.5.1, pinned in `package.json` / `package-lock.json` and installed by `make setup` (`npm ci`) | `node --version`; `node_modules/.bin/promptfoo --version` |
| Docker | optional — only for the devcontainer | — |
| Install | `make setup` (installs the `phoenix` extra and the `dev` group, registers the `stockroom` Jupyter kernel) | `uv run stockroom config` prints `mode=mock` |
| Tests | `make test` (unit + golden regression; writes `reports/eval_results.json`) | all green, no AWS variables set |
| Data | `make validate-data` | prints schema and coverage checks |
| Devcontainer | `.devcontainer/devcontainer.json`: Python 3.12 image, Node 22, AWS CLI, forwards ports 6006 (Phoenix) and 8765 (MCP); `postCreateCommand` installs uv and runs `make setup` | open in VS Code / Codespaces |
| Colab / SageMaker | the notebooks' setup cell checks `find_spec("stockroom")` and otherwise clones `genial-labs-ai/system_agent_harness_aws` and `%pip install`s it (DECISIONS entry 2) | run the first cell; the banner from `detect_mode()` should say `MOCK` |
| Phoenix (Day 2, optional) | `make phoenix` serves on port 6006; set `STOCKROOM_TRACE_EXPORTER=phoenix` | open `http://localhost:6006` |
| Promptfoo offline | `make promptfoo` and `make slides` run the local binaries in `node_modules/.bin` (never an npx download); `PROMPTFOO_DISABLE_UPDATE=1` and `PROMPTFOO_DISABLE_TELEMETRY=1` are exported by the Makefile | `make promptfoo` works with the network off |

Pinned versions are printed by `make pins` and recorded in `docs/DECISIONS.md` (phase-1 entry).

### Live-mode prerequisites (optional, Days 2 and 4)

Summarised here; the README (phase 7) is the authoritative list.

- AWS credentials resolvable by boto3 (profile or role), `AWS_REGION`.
- Model access in Bedrock for the two models you choose, set as `AGENT_MODEL_ID` and
  `JUDGE_MODEL_ID` (cross-region inference profile IDs; list with
  `aws bedrock list-inference-profiles --type-equals SYSTEM_DEFINED`). Anthropic models need the
  one-time use-case form and Marketplace subscription permissions on first invocation
  (`docs/aws/README.md`).
- `STOCKROOM_MODE=live` explicitly, or leave it unset and let `detect_mode()` pick live when
  credentials *and* both model IDs are present.
- For S3 publishing: an existing bucket in `S3_BUCKET` (and optionally `S3_PREFIX`); the IAM
  policy in `docs/aws/iam_policy.json` scoped to it.
- For the live (manually dispatched) workflow: the OIDC provider, the role with `docs/aws/oidc_trust_policy.json`
  and `docs/aws/iam_policy.json`, and the repository variables `AWS_REGION`, `AWS_OIDC_ROLE_ARN`,
  `AGENT_MODEL_ID`, `JUDGE_MODEL_ID`, `S3_BUCKET`.
- For the Bedrock Evaluations lab: a separate service role and `CreateEvaluationJob` permissions
  (`docs/aws/README.md`); jobs are only submitted with `STOCKROOM_CONFIRM_AWS_SPEND=1`.
- For CloudWatch GenAI observability / AgentCore Evaluations: install `--extra cloudwatch`
  instead of `--extra phoenix` (they conflict; DECISIONS phase 1) and enable Transaction Search.
- Cost: live mode prints an estimated cost per run (`RunResult.print_cost_summary()`); Nova is
  priced from the public price list, Claude prices are unknown unless `AGENT_PRICE_INPUT_PER_1K` /
  `AGENT_PRICE_OUTPUT_PER_1K` are set. Never run live without telling participants what it costs.

---

## 3. Common participant failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `uv sync` resolves a different Python, or `ModuleNotFoundError: stockroom` | wrong interpreter (system 3.11/3.13 or a global venv) | `uv python install 3.12 && make setup`; in notebooks pick the kernel **Python (stockroom)**; check `uv run python --version` |
| Notebook kernel missing / cells import the wrong `stockroom` | kernel not registered, or a stale one from another checkout | `uv run python -m ipykernel install --user --name stockroom --display-name "Python (stockroom)"` (what `make setup` does); restart Jupyter |
| `make promptfoo` says Promptfoo/Marp are not installed | `npm ci` has not run | run `make setup` once with network access; afterwards everything runs offline. `PROMPTFOO_PYTHON` must point at `.venv/bin/python` (the Makefile exports it) |
| `make eval` / `make ci` fails with `tool_selection_accuracy = 0.64` and nobody changed anything | `STOCKROOM_WEAKNESSES` left exported from the Day 3 lab | `unset STOCKROOM_WEAKNESSES`; confirm with `uv run stockroom config` (`weaknesses=none`) |
| Notebook cells fail with `NameError` or an empty `RunResult` | cells run out of order (the setup cell and the config cell must run first) | *Kernel → Restart & Run All* up to the failing exercise; the `check` cells after each exercise are meant to be run after the exercise cell |
| `make mcp-server` says the address is in use | a server from an earlier `make ci` or a stray terminal still bound to 8765 | `kill $(cat reports/.mcp.pid)` or `lsof -i :8765`; or run with `MCP_PORT=8766 make mcp-server` and `STOCKROOM_MCP_URL=http://127.0.0.1:8766/mcp` |
| `STOCKROOM_TOOL_TRANSPORT=mcp-http make eval` fails immediately | no server running | `make mcp-server` in another terminal, then `make mcp-smoke` |
| Day 2 traces not visible | Phoenix not running, or exporter not set | `make phoenix` (port 6006) in another terminal; `STOCKROOM_TRACE_EXPORTER=phoenix`; the memory exporter still works without it |
| `ConfigError: Live mode needs AGENT_MODEL_ID and JUDGE_MODEL_ID` | `STOCKROOM_MODE=live` without model IDs | set both IDs, or `unset STOCKROOM_MODE` to fall back to mock |
| `ConfigError: Unknown weakness flag(s)` | typo in `STOCKROOM_WEAKNESSES` | valid flags: `ambiguous_tool_desc`, `oversized_payload`, `naive_retry`, `injection_unguarded` |
| `AccessDeniedException` in live mode | model access, use-case form, or Marketplace subscription missing | `uv run python scripts/sync_datasets_s3.py check-models`; fix access rather than widening IAM |
| Thresholds step says "results file not found" | `make thresholds` run before `make eval` | run `make eval` (or `make test`) first |
| Baseline regression failure on a fresh clone | no `reports/baseline/main.json` yet, or it was generated with a flag on | `make baseline` from a clean mock run; check `mode`/`weaknesses` inside the file |
| Lecture/slide references drift after refactors | a renamed function or moved file | `uv run python scripts/check_lecture_refs.py` (add it to your pre-commit) |

---

## 4. Facilitation notes per exercise

### Day 1 — Deterministic and RAG Evals

- *Taxonomy demo on the weakness flags.* Show the same query under each flag using
  `uv run stockroom run "<query>" --case-id <id> --json`. Ask participants to name the failure
  class before revealing the flag.
- *Golden-set tour.* Walk `data/golden/DATASET_CARD.md`, then one case per category. Emphasise the
  sourcing note: expected tools were derived from the data files, not from running the agent.
- *DeepEval assertions.* Keep the model fake; the point is the shape of `LLMTestCase` with tool
  calls. Warn that `deepeval test run` is not used (pytest parametrisation instead).
- *RAGAS.* The BM25 branch is local; the Knowledge Base branch is live-only and skipped in mock mode.
  Expect a question about `langchain-community` constraints — the answer is in DECISIONS phase 1.
- *New golden cases.* Require `expected_facts` **and** `forbidden_facts`; run
  `make validate-data` to catch unknown SKUs/orders and coverage gaps.

### Day 2 — Judge Calibration and OTel Traces

- *Spans.* Start with the memory exporter so nobody is blocked on Phoenix. `span_tree()` prints the
  hierarchy; `ToolCallEvaluator.from_spans()` must give the same `loops` / `repeated_identical` as
  `ToolCallEvaluator.from_run()` — make them check.
- *Calibration v1 → v2.* Run `calibrate()` with `FakeJudge("v1")` then `FakeJudge("v2")` and compare
  `agreement_rate()` and `cohens_kappa()`. The items the README calls "misleading but string-correct"
  (C23–C24) are the discussion hook: no rule-based judge catches them.
- *Bias probes.* `run_all_probes()` on the position/verbosity/self-preference groups. Ask what a
  *live* judge would need to pass before you trust it in the nightly run.

### Day 3 — Building a Custom Agent Harness (see the lecture's lab plan)

- *Construction lab (section 8, Exercises 1–2).* `injection_unguarded` stays on while participants
  assert on the G041 trajectory and build a run guard through `Harness(run_guards=...)`. Make sure
  they can explain why `answer_correctness` stays at 0.94 (the prompt leak is in the text, not an
  action) and why `invalid_call_rate` rises (blocked calls count as not executed). Contrast it with
  the payload wrapper (Exercise 3): a run guard sees calls, a wrapper sees results.
- *What the mechanisms do not guarantee.* Sections 3–5 end with demos of the limits (the token
  budget is a ceiling on `chars/4` estimates, compaction keeps a prefix, quarantine is a regex);
  use them in the review discussion.
- *Guards, validation, compaction.* Start every participant with `StockroomConfig.mock()` overrides
  rather than env vars, so nothing leaks into later exercises. Read `RunResult.transition_log`
  aloud for one run.
- *MCP server via client.* In-memory first (`build_server()` + `McpToolExecutor(server=...)`), then
  stdio; the HTTP path only for those who want to mirror CI. Point out `HIDDEN_TOOLS` and the
  per-run `reset_session_state` call.
- *Toggle each weakness and chart.* The chart should show four bars per metric. The "aha" is
  `ambiguous_tool_desc`: tool selection 0.64, answer correctness 0.78 — have participants explain
  the gap before you do.
- *Fix one at a time.* Fixing means clearing the flag and explaining which code path closes the
  failure, **not** deleting the flagged branch (`test_every_flag_moves_at_least_one_gate_metric()`
  guards against that).

### Day 4 — Bedrock Evaluations and CI Gating (see the lecture's lab plan)

- *Label validation (Exercise 1).* The point is the order: validate against the data first, run
  the agent second. Ask who would have kept S012 (the agent passes it; the label is the reorder
  point) and dropped S013 (the agent fails it; the label is right). S007 is a near-duplicate the
  template teacher produced by accident; it is a good prompt for "what is a duplicate?".
- *Red-team cases (Exercise 2).* Every case needs a trajectory assertion (`no_tool_called` or
  `expect_tools`), a paraphrase, and an assertion that fails on a successful attack. Show
  `quarantined` in the provider output.
- *Floor, noise, allowed regression (Exercise 3).* The illustrative runs are synthetic and labelled
  so. In mock mode `run_to_run` has sd 0, which is the teaching point: reproducibility, not
  reliability. If the group has a live nightly artefact, read its `run_to_run` block and the
  summary's noise warnings instead.
- *Capstone (Exercise 4).* The aggregate-only gate passes `naive_retry` and `oversized_payload`;
  participants change the gate, keep `main` green and write the review. Accept any rule the evidence
  supports (per-category termination, a token-cost limit, a new golden case); insist that the review
  says what happens to the baseline. Their before/after summaries land in `reports/day4/`.
- *`make ci` fail then pass.* Run the failing one with the flag exported in **that** shell only:
  `STOCKROOM_WEAKNESSES=naive_retry make ci` (or `ambiguous_tool_desc`). Keep both
  `reports/summary.md` files.
- *Bedrock Evaluations payload.* Build the JSONL; validate ≤1000 lines and a single
  `modelIdentifier`. Do not submit in class unless the account owner has agreed to the spend and
  `STOCKROOM_CONFIRM_AWS_SPEND=1` is set deliberately.
- *AgentCore `evaluate`.* Live only; needs spans in CloudWatch, which take a couple of minutes to
  appear. Treat it as a demo unless the group has the AWS side ready.

---

## 5. Regenerating data, baseline and notebooks

| What | Command | When |
|---|---|---|
| Seed data (catalogue, orders, suppliers) | `uv run python scripts/generate_seed_data.py` then `make validate-data` | only if you change the generator; the golden set references specific SKUs/orders |
| Validate data (schemas, cross-references, coverage, manifest hash) | `make validate-data` | after any edit under `data/`; `scripts/validate_data.py --write-manifest` regenerates `data/golden/manifest.json` after a golden change (bump the version and add a changelog line to the dataset card) |
| Eval results | `make eval` (golden regression only) or `make test` (everything) | before `make thresholds` |
| Baseline | `make baseline` → `reports/baseline/main.json` | after an intentional metric/rubric/description/golden change, from a clean mock run with no flags; explain the diff in the PR |
| Notebooks | `make build-notebooks` (student + `solutions/` from `notebooks/src/*.py`); `make notebooks` builds and executes them | after editing a notebook source; never edit the generated `.ipynb` |
| Slides | `make slides` renders `slides/DAY1_MOTIVATIONAL_SLIDES.md` with Marp | after editing the deck |
| Pricing table | `uv run python scripts/fetch_pricing.py` | occasionally; the only non-live network call in the repo, never in CI |
| Lecture reference check | `uv run python scripts/check_lecture_refs.py` | after renaming anything the lectures cite |
| Style check | `uv run python scripts/check_style.py` (titles from `docs/workshop.yml`, `docs/STYLE_GUIDE.md`) | after renaming a day, lab or deck, or editing prose |
| Whole PR gate | `make ci` | before opening a PR |

---

## 6. Before you teach: a 15-minute self-check

1. Fresh shell, no `STOCKROOM_*` / `AWS_*` variables: `make setup && make test`.
2. `make mcp-server` in one terminal, `make mcp-smoke` in another; stop the server.
3. `make promptfoo` with the network off.
4. `STOCKROOM_WEAKNESSES=ambiguous_tool_desc make eval && make thresholds` fails with the 0.64 row;
   `make eval && make thresholds` passes.
5. `uv run python scripts/check_lecture_refs.py` and `uv run python scripts/check_style.py` are clean.
