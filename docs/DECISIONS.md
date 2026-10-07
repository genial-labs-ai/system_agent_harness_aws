# Decisions, deviations and version pins

This file records every place where the delivered repository deviates from the project brief, makes
a judgement call the brief left open, or pins a version that was verified against current
documentation. Entries are grouped by the phase in which they were made. Dates are absolute.

## Phase 1 — scaffold (2026-10-06)

1. **Repository root.** The brief's layout shows a top-level `agent-evals-aws-workshop/` folder. The
   working directory is itself the repository root (`agent_systems_harnesses_aws`), so the layout is
   applied directly at the root without a nested folder.
2. **GitHub org/repo placeholder.** The target GitHub repository was not yet decided, so
   `<GITHUB_ORG>/<GITHUB_REPO>` is used in `docs/aws/oidc_trust_policy.json`, the README clone
   commands, the notebook setup cells (`REPO_URL`) and the CI PR-comment step. Replace it with a
   single search-and-replace before publishing.
3. **OpenTelemetry version range instead of a single pin.** `arize-phoenix-otel==0.17.2` requires
   `opentelemetry-exporter-otlp-proto-http>=1.45`, while `aws-opentelemetry-distro==0.21.0` (ADOT,
   needed for CloudWatch GenAI observability in live mode) pins `opentelemetry-sdk==1.44.0`. The two
   cannot be installed together. The core dependencies therefore allow `>=1.44.0,<1.46`, and the
   `phoenix` and `cloudwatch` extras are declared as **conflicting extras** in `[tool.uv]`. Local
   development installs `--extra phoenix` (resolves OTEL 1.45.1); a live-mode runner that wants
   CloudWatch installs `--extra cloudwatch` instead (resolves OTEL 1.44.0). Nothing in `src/` depends
   on the difference.
4. **`langchain-community` constraint.** `ragas==0.4.3` imports
   `langchain_community.chat_models.vertexai` at import time, which `langchain-community>=0.4`
   removed, so a uv `constraint-dependencies` entry holds it at `>=0.3.27,<0.4`. We do not use any
   LangChain functionality ourselves (see the RAGAS decision in phase 3).
5. **Prices come from the public AWS Price List bulk API, not from the pricing web page.** The
   pricing page is rendered client-side and cannot be scraped reliably, whereas
   `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json`
   is a public, credential-free feed. It contains Amazon Nova on-demand token prices but **not**
   third-party models billed through AWS Marketplace (Anthropic Claude 4.x and 5.x). Those entries
   are `null` in `pricing.yaml`; the harness prints "cost unknown" for them unless
   `AGENT_PRICE_INPUT_PER_1K` / `AGENT_PRICE_OUTPUT_PER_1K` (and the `JUDGE_PRICE_*` twins) are set.
   No price is ever guessed. `scripts/fetch_pricing.py` regenerates the file (the only non-live
   network call in the repository; never run in CI).
6. **`act` is not used for the local workflow check.** It is not installed on the authoring machine;
   `make ci` runs the same steps as `.github/workflows/agent_eval_ci.yml` in the same order instead.
7. **Phase-1 verified versions** (installed and import-tested): deepeval 4.2.8, ragas 0.4.3,
   mcp 2.3.0, boto3 1.43.108 / botocore 1.43.108, opentelemetry-sdk 1.45.1,
   opentelemetry-semantic-conventions 0.66b1, openinference-semantic-conventions 0.1.41,
   arize-phoenix 20.19.0, arize-phoenix-otel 0.17.2, pydantic 2.13.5, jsonschema 4.26.0,
   pytest 9.1.1, ruff 0.16.10, jupytext 1.19.6, nbmake 1.5.5, ipykernel 7.4.0; npm promptfoo 0.124.0,
   @marp-team/marp-cli 4.5.1. GitHub Actions: actions/checkout v7, astral-sh/setup-uv v10,
   actions/setup-node v7, actions/upload-artifact v7, actions/github-script v9,
   aws-actions/configure-aws-credentials v6.3.0. The full transitive set is in `uv.lock`.

## Phase 3 — core code (2026-10-06)

8. **The mock "model" is a deterministic simulator of failure classes, not a language model.**
   `FakeBedrockClient` replays scripted turns for golden cases that have a `mock_script`
   (schema drift, multi-step) and otherwise uses `HeuristicPlanner`, which selects tools *only* by
   matching an intent phrase against tool descriptions (so an ambiguous description really
   captures calls), re-issues an identical call after a transient error (so a missing guard
   really loops), and follows instruction-like lines in tool output unless the harness
   quarantined them. Scripted replays alone could not show metric movement when a description
   or a guard changes, which the Day 3 lab depends on. Mock-mode scores are therefore an upper
   bound; the nightly live run reports real numbers.
9. **Seeded weaknesses are feature flags** (`STOCKROOM_WEAKNESSES`), default off, so the shipped
   repository passes its own gate while Day 3 can switch each failure on. The `naive_retry`
   flag disables both the tool's bounded internal retry *and* the harness `RepeatedCallGuard`
   (the "retry path that can loop"); with the flag off the legacy shard still fails its first
   attempt per order so the fix is visible in traces.
10. **Tool-output quarantine as the injection defence.** With `injection_unguarded` off the
    harness removes whole paragraphs that match instruction-like patterns from tool results and
    marks them; this is a demonstrable, testable mitigation, not a complete defence, and the Day 3
    lecture says so.
11. **Token estimates are `len(text) // 4` in mock mode** so budgets and compaction trigger
    deterministically; live mode uses the `usage` block returned by Converse.
12. **DeepEval is run through plain `pytest`, not `deepeval test run`.** The docs recommend the
    latter, but the workshop needs `@pytest.mark.parametrize` over the golden set, a
    session-scoped results collector and no Confident AI account. Metrics are *measured*
    (`metric.measure`) and recorded; only hard invariants assert, so one regressed case yields a
    metrics table rather than 50 red tests. `DEEPEVAL_TELEMETRY_OPT_OUT=1` is set everywhere.
13. **RAGAS via a custom `InstructorBaseRagasLLM`** (`StockroomRagasLLM`) rather than
    `llm_factory`/LangChain/litellm wrappers: the collections metrics only need
    `generate(prompt, response_model)`; this keeps the Bedrock judge and the deterministic fake
    on one code path and avoids an OpenAI dependency. Only `Faithfulness`, `ContextPrecision`
    and `ContextRecall` are used because they need no embeddings model.
14. **OpenTelemetry GenAI semantic conventions are "Development" status.** Attribute names are
    centralised in `evals/semconv.py` and cross-checked in `tests/test_tracer.py` against the
    installed `opentelemetry-semantic-conventions` incubating module when present. Spans also
    carry `openinference.span.kind` so older Phoenix versions render them; Phoenix ≥ 15.10
    converts `gen_ai.*` automatically.
15. **MCP Python SDK v2 (`mcp==2.3.0`).** The brief predates the rename of `FastMCP` to
    `MCPServer`; the server uses `mcp.server.MCPServer`, the client `mcp.Client`, and tests use
    the in-memory `Client(server)` connection. A hidden `reset_session_state` tool resets the
    per-run ledger across the process boundary; it is filtered out of the tool list the model sees.
16. **The harness advertises whatever the tool server describes.** Over MCP, the ambiguous
    description flag set on the *server* process leaks through the boundary even when the harness
    config is clean (`tests/test_mcp_server.py::test_executor_over_stdio_subprocess`); this is
    deliberate and is a Day 3 talking point.

## Phase 4 — eval suite and CI (2026-10-06)

17. **Red-team cases are hand-written Promptfoo tests with deterministic assertions.**
    `promptfoo redteam generate` needs Promptfoo's remote generation service or an OpenAI key, and
    the brief limits red teaming to the Stockroom agent; eight cases cover the four categories
    (jailbreak resistance, indirect injection via tool output, system-prompt leakage, unsafe tool
    use). No `llm-rubric` assertions are used, so the Promptfoo step needs no model key at all.
18. **The Promptfoo provider returns a JSON envelope** (`answer`, `tools`, `termination`,
    `quarantined`, `steps`) so assertions can check trajectories, not just text; `scripts/promptfoo_asserts.py`
    holds the assertion functions.
19. **Amazon Bedrock Evaluations has no agent/trajectory job type** (job types today: automatic,
    LLM-as-a-judge, human, RAG — `applicationType` is `ModelEvaluation` or `RagEvaluation`). The Day 4
    lab therefore uses it for *final answers* through the bring-your-own-inference-responses dataset
    format and shows AgentCore Evaluations' on-demand `evaluate` API for trajectories. Jobs are only
    submitted when `STOCKROOM_MODE=live` and `STOCKROOM_CONFIRM_AWS_SPEND=1`.
20. **Thresholds.** The gate keeps the brief's 0.85 for tool-selection accuracy and answer
    correctness, adds hard invariants (`must_not_call_ok_rate` 1.0, `loop_rate` ≤ 0.10,
    `termination_match_rate` ≥ 0.90), a red-team gate (0 successful attacks) and a regression
    check against the committed `reports/baseline/main.json` (max drop 0.05). Mock mode is
    deterministic (variance 0), so variance-based thresholds only make sense for the nightly live
    run, which reports 95% confidence intervals from `STOCKROOM_EVAL_REPEATS` instead of gating.
21. **CI runs the regression suite over the MCP streamable-HTTP transport** (server started as a
    background step and smoke-tested) so the process boundary is exercised on every PR; `make ci`
    mirrors the same steps locally because `act` is unavailable.
22. **Nightly workflow guard.** It is skipped unless the repository variable `AWS_OIDC_ROLE_ARN` is
    set, so forks and the un-configured upstream never fail; it never blocks PRs. Model/bucket IDs
    come from repository variables, never from the workflow file.
