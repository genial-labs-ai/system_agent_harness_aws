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
