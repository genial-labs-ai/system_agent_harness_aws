# Decisions, deviations and version pins

This file records every place where the delivered repository deviates from the project brief, makes
a judgement call the brief left open, or pins a version that was verified against current
documentation. Entries are grouped by the phase in which they were made. Dates are absolute.

## Phase 1 — scaffold (2026-10-06)

1. **Repository root.** The brief's layout shows a top-level `agent-evals-aws-workshop/` folder. The
   working directory is itself the repository root (`agent_systems_harnesses_aws`), so the layout is
   applied directly at the root without a nested folder.
2. **GitHub org/repo.** The target repository was not decided during the build, so a
   `<GITHUB_ORG>/<GITHUB_REPO>` placeholder was used in `docs/aws/oidc_trust_policy.json`, the
   README clone commands, the notebook setup cells (`REPO_URL`) and the docs. On 2026-10-07 it was
   replaced everywhere with `genial-labs-ai/system_agent_harness_aws` (the local checkout keeps its original
   directory name `agent_systems_harnesses_aws`).
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
   @marp-team/marp-cli 4.5.1. GitHub Actions: actions/checkout v7, astral-sh/setup-uv v10.2.0,
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
    set, so forks and the un-configured upstream never fail; it never blocks PRs. (Since entry 37 it
    also has no schedule.) Model/bucket IDs
    come from repository variables, never from the workflow file.

## Phase 6 — teaching materials (2026-10-06/07)

23. **Lectures and slides quote mock-mode numbers only, labelled as upper bounds.** Every figure in
    `lectures/` and `slides/` (tool selection 1.00 → 0.64, kappa 0.25 → 0.83, 9.4k vs 1.9k input
    tokens, …) was reproduced by running the repository in mock mode; live numbers come from the
    nightly report. No external incidents, company names or statistics are used.
24. **The deck's cost example uses Amazon Nova Pro's published price as a labelled illustration**
    because the Claude entries in `pricing.yaml` are `null` (Marketplace-billed models are absent
    from the Price List API); the slide states this explicitly.
25. **Bias probes ship one pair per probe group.** `data/judge_calibration` contains one position
    pair (both orders), one verbosity pair and one self-preference pair: enough to demonstrate the
    mechanism and to show the deterministic fake judge is unbiased by construction, not enough to
    characterise a live judge. The Day 2 lecture says so and asks participants to add pairs before
    trusting the probe on Bedrock.
26. **Node tooling is pinned in `package.json` + `package-lock.json`** (promptfoo 0.124.0,
    @marp-team/marp-cli 4.5.1) and installed with `npm ci` by `make setup` and CI (with the
    `setup-node` npm cache) instead of relying on an ad-hoc `npx` download: the first Promptfoo
    download is several minutes on a slow connection and the lockfile makes the tool versions
    reproducible. `make promptfoo` / `make slides` still work with the npx cache alone.
27. **`scripts/check_lecture_refs.py` resolves symbols generously.** Besides functions, classes
    and methods found by AST, it accepts attribute, parameter and keyword names used anywhere in the
    code base, identifier-shaped string constants, the vocabulary of `eval_thresholds.yaml` and
    `promptfooconfig.yaml`, bare file names in the repo, and an explicit ignore list for shell
    commands, IAM actions, model/profile IDs, case IDs and environment-variable names. This keeps the
    phase-6 check ("every code reference in the lectures points to a real file and function")
    strict for paths and `name()` references without flagging prose; missing notebooks are warnings
    until `make build-notebooks` has run.

## Phase 5 — notebooks (2026-10-07)

28. **Notebook sources are jupytext percent files under `notebooks/src/`; the `.ipynb` files are
    generated** by `scripts/build_notebooks.py`. Cell tags drive the two outputs: `exercise` cells
    go to the student notebook only, `solution` cells to `notebooks/solutions/` only (same position),
    `check` cells to both and are written to print "not solved yet" rather than fail in the student
    variant. Generated notebooks carry no outputs, kernelspec `python3`, and content-hash cell ids
    (`co-<sha1[:8]>`) so rebuilds are byte-identical and diffs stay clean (nbformat would otherwise
    assign random ids). The participant marker is referenced only in the builder, as `"TO" + "DO"`,
    so a repository-wide grep hits exercise cells only.
29. **Notebook sources are not importable modules by design.** They contain IPython magics
    (`%pip`) and top-level `await`, because the RAGAS collections metrics are coroutine-only and
    `asyncio.run` cannot be used inside the kernel's running loop. `ruff` excludes `notebooks/`.
30. **Setup cell for Colab / SageMaker.** `%pip install` is guarded by
    `importlib.util.find_spec("stockroom")` (a no-op when the repo is installed), pins the versions
    from `pyproject.toml`, and adds `langchain-community>=0.3.27,<0.4` explicitly because uv's
    `constraint-dependencies` do not apply to plain pip (ragas 0.4.3 fails to import with
    langchain-community 0.4). `REPO_URL` points at `genial-labs-ai/system_agent_harness_aws` (entry 2).
31. **Phoenix export in notebooks is opt-in via `PHOENIX_COLLECTOR_ENDPOINT`**, not via importability:
    an OTLP exporter with no collector would stall nbmake on export timeouts. The Day 2 cell prints
    how to start Phoenix (`make phoenix`) when the variable is unset.
32. **`MaxToolPayloadGuard` (Day 3 exercise) is a `ToolExecutor` wrapper, not a harness guard**,
    because `Harness` exposes no guard-registration hook; it replaces any single tool result over N
    characters with a structured `payload_too_large` error, which turns G049 under
    `oversized_payload` from `TOKEN_BUDGET` into `COMPLETED` (14,093 → 2,777 input tokens). A
    guard-plugin seam on the harness is a candidate follow-up.
33. **Bedrock Evaluations payloads are validated offline against botocore's service model**
    (`botocore.validate.validate_parameters` on the `CreateEvaluationJob` input shape shipped with
    botocore 1.43.108), so the Day 4 notebook proves the request is well-formed without an AWS call.
    `taskType` is `QuestionAndAnswer`, metrics `Builtin.Correctness/Completeness/Faithfulness/Helpfulness`,
    `jobName` uses `1-0-0` because the pattern forbids dots. Submission is triple-gated
    (`STOCKROOM_MODE=live`, `STOCKROOM_CONFIRM_AWS_SPEND=1`, `EVAL_ROLE_ARN`). The AgentCore
    `evaluate()` cell additionally needs `AGENTCORE_SESSION_ID`, since the mock harness does not run
    on AgentCore Runtime.
34. **Day 4 Exercise 2 uses an explicitly illustrative variance table** (labelled made-up) as input
    for setting a threshold with the rule "mean − 2·pstdev, rounded down", presented as one
    reasonable rule rather than a standard.
35. **`make slides` redirects stdin from `/dev/null`.** marp-cli treats a non-TTY stdin as an
    additional markdown input; under `make ci` (and on CI runners) stdin is an open pipe, so the
    render hung indefinitely waiting for it, and with a closed pipe it fails with "Output path
    cannot specify with processing multiple files". Redirecting stdin makes the render
    deterministic in both environments.
36. **`astral-sh/setup-uv` is pinned to the exact tag `v10.2.0`.** The first GitHub-hosted run of
    `agent_eval_ci.yml` (after publishing to `genial-labs-ai/system_agent_harness_aws` on
    2026-10-07) failed in "Set up job" with "unable to find version `v10`": that action publishes
    release tags only (v10.2.0), not a moving `v10` major tag, unlike the `actions/*` actions. It
    never surfaced locally because `make ci` replaces `act` (DECISIONS phase 4).
37. **The live workflow is `workflow_dispatch`-only; the daily cron was removed** (user decision,
    2026-10-07) so Bedrock spend happens only when an instructor asks for a run, e.g. before a
    cohort. The file name `agent_eval_nightly.yml`, the workflow name and the "nightly tier"
    wording in the teaching materials are kept: "nightly" names the scheduled-live layer of the
    offline / nightly / online model taught on Day 4, and the materials now say explicitly that
    this repository dispatches that layer by hand.
38. **Repository hygiene files added on 2026-10-07 (session 3).** `LICENSE` is MIT with
    "Genial Labs" as holder, matching the `license = MIT` already declared in `pyproject.toml` and
    `package.json` (there was no LICENSE file, so GitHub showed no licence). `CITATION.cff` lists
    the git author as the only author; add co-authors there, not in the README. Dependabot runs
    **monthly and grouped** rather than weekly because every pin is recorded in the README table
    and `docs/DECISIONS.md`, and because a library bump that moves a metric also needs
    `make baseline` plus an explanation; a weekly stream of ungrouped PRs would make that
    discipline impossible. The labeler workflow uses `pull_request_target` (the documented
    pattern for `actions/labeler`, so it can label PRs from forks) and checks out nothing, so it
    never runs fork code with write permissions. `SECURITY.md` points at GitHub private
    vulnerability reporting, which was enabled on the repository the same day; it states
    explicitly that the seeded weaknesses and the planted injection are lab features.
39. **The website is built with Quarto 1.6.40 and deployed to GitHub Pages from `pages.yml`**
    (user decision, 2026-10-07). Quarto was chosen over MkDocs Material and Jupyter Book because
    the sources are Markdown lectures with many Mermaid diagrams, executed-notebook pages and a
    reveal.js deck, all of which Quarto renders without plugins. Three choices follow from the
    repo's rules: (a) the lectures and docs stay GitHub-flavoured Markdown and are rendered
    **unchanged** through `site/filters/gfm.lua`, which promotes the first H1 to the page title
    and turns ```mermaid fences into `<pre class="mermaid">` blocks rendered by Mermaid 11 from
    jsDelivr at view time (Quarto's native diagram engine only handles `{mermaid}` cells in
    `.qmd` files, and adding YAML front matter to the lectures would render as a table on GitHub);
    (b) the committed notebooks carry no outputs, so `notebooks/_metadata.yml` makes the site
    build **execute them in mock mode** with the project virtualenv (`QUARTO_PYTHON`), which is
    the same offline run `make notebooks` performs, and `make site` then regenerates the output-free
    notebooks from `notebooks/src` because Quarto writes executed outputs back into the `.ipynb`; (c) the site build is **not part of the PR
    gate**: Quarto is a binary outside `uv`/`npm`, so `make site` is opt-in locally and runs in
    CI only on pushes to `main`. `quarto-dev/quarto-actions/setup` is pinned to the exact tag
    `v2.2.0` because, like `setup-uv`, it publishes no moving major tag. Pages was switched to
    the "GitHub Actions" build type on the repository the same day. The kickoff deck
    `slides/intro.qmd` is Quarto reveal.js rather than Marp so it lives inside the site; the
    Day 1 Marp deck is still built by `make slides` and copied in as a static resource. Both
    `slides/*.qmd` and `index.qmd` are now scanned by `scripts/check_lecture_refs.py`.

40. **The website has its own design, shared with the sister workshop site rather than a stock
    Bootswatch theme** (user request, 2026-10-07: the default-looking site was to be brought up to
    the standard of other workshop sites). `site/_tokens.scss` defines every colour as a token, `site/theme.scss` imports it and
    holds every rule (and publishes the tokens as `--ws-*` custom properties for the Mermaid
    script, so the diagram palette is not a second copy); `site/theme-dark.scss` overrides only the tokens, so one set of rules serves
    both themes and the dark theme is `cosmo` plus the overrides instead of a second Bootswatch
    theme (`darkly`, which fought the custom rules). Inter and Source Serif 4 are vendored as
    latin-subset variable fonts under `site/fonts/` (SIL OFL, from Fontsource) so the site depends
    on no font CDN; Mermaid 11 is the one remaining CDN script. The landing page uses
    `page-layout: full` with a `landing` body class and no sidebar: the sidebar now has an `id`,
    and Quarto shows an id'd sidebar only on the pages it lists. `site/filters/gfm.lua` splits a
    lecture or notebook H1 of the form "Day N — rest" (for notebooks, the `title` Quarto's
    jupyter engine has already taken from that heading) into the title ("rest") and a subtitle
    ("Day N · lecture notes" / "lab notebook") that the theme sets as an eyebrow above the title;
    the Markdown sources are still unchanged for GitHub. The Mermaid script reads the palette from the
    `--ws-*` custom properties once the stylesheet Quarto's own script swaps in has applied
    (Quarto sets the body class and swaps the stylesheet after the module has run), and
    redraws the diagrams when the navbar toggle changes the body class. The site's numbers (4 days, 8
    notebooks, 5 tools, 4 weaknesses, 50 golden cases, the 1.0 → 0.64 tool-selection drop) are
    the repo's own: `data/golden/manifest.json`, `README.md` and the committed baseline.
