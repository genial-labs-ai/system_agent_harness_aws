# Contributing

Thanks for helping make the workshop better. This page is the short version; `AGENTS.md` holds the
full ground rules (they apply to humans and coding agents alike), and `docs/DECISIONS.md` records
every deviation from the brief and every version pin.

## Ground rules

1. **Offline-first.** `make test`, `make notebooks` and `make ci` must pass with no AWS credentials
   and no network. Live AWS is opt-in through `STOCKROOM_MODE=live`.
2. **No hardcoded model IDs.** Model IDs, region and budgets come from the environment
   (`src/stockroom/config.py`). Never bake a model ID into a code path.
3. **Verify APIs, don't recall them.** Before using a DeepEval, RAGAS, Promptfoo, MCP, Phoenix,
   Bedrock or AgentCore API, check the installed package or the current docs. Record new pins in
   `docs/DECISIONS.md` and the README table.
4. **Judges run on Bedrock, never OpenAI.** No `OPENAI_API_KEY` anywhere.
5. **No fabricated facts** in lectures, slides or docs. Figures quoted in teaching materials must be
   reproduced from the repo in mock mode.
6. **No stubs.** No `pass`, `...` or `TODO` in delivered code. `TODO` is allowed only in tagged
   `exercise` cells in `notebooks/src/*.py`, each with a matching `solution` cell.
7. **Seeded weaknesses are feature flags.** Do not "fix" one by deleting the flagged branch; the
   Day 3 lab depends on switching it on.

## Setup

```bash
git clone https://github.com/genial-labs-ai/system_agent_harness_aws.git
cd system_agent_harness_aws
make setup      # uv sync, Jupyter kernel, npm ci (Promptfoo + Marp)
make test       # unit tests + golden-set regression
```

Or open the repo in a Codespace / devcontainer: `.devcontainer/devcontainer.json` runs `make setup`
for you and forwards the Phoenix (6006) and MCP (8765) ports.

## Workflow

- Branch from `main`. Keep PRs focused: one failure mode, one lab, one integration.
- Run `make ci` before pushing. It is the same gate the `agent-eval-ci` workflow runs.
- Python: 3.12, `uv`, `ruff` (line length 100), type hints on public functions, pydantic models for
  data that crosses a boundary, dataclasses for internal config. `make lint` / `make format`.
- Notebooks: edit `notebooks/src/*.py` (jupytext percent format) and run `make build-notebooks`.
  The `.ipynb` files are generated; do not edit them by hand.
- Teaching materials: cite paths as `src/stockroom/...py` and symbols as `name()` so
  `scripts/check_lecture_refs.py` can verify them. Titles, product names and terms follow
  `docs/STYLE_GUIDE.md`; rename a day, lab or deck in `docs/workshop.yml` first, then run
  `uv run python scripts/check_style.py` to find every copy.
- Finished a session? Update `docs/TASKS.md` (and `docs/DECISIONS.md` for any deviation).

## Changing a metric, rubric, tool description, golden case or threshold

These changes move the committed baseline (`reports/baseline/main.json`) or the gate itself
(`eval_thresholds.yaml`), and CI compares every PR against that baseline.

1. Make the change and run `make test`.
2. Run `make baseline` to regenerate `reports/baseline/main.json` deliberately.
3. In the PR, paste the before/after metric rows and explain why the movement is expected.
   A drop of more than 0.05 on any metric fails the gate on purpose.

## Cost and credentials

Nothing in CI, tests or notebooks may create AWS resources or incur spend without an explicit
human go-ahead. The live workflow is `workflow_dispatch`-only and authenticates through GitHub
OIDC (`docs/aws/`); never add long-lived keys. Live runs enforce `MAX_TOKENS`, `MAX_STEPS` and
`TOKEN_BUDGET` and print an estimated cost.

## Reporting problems

Use the issue templates: **Bug report** for the agent, evals or tooling, **Lab or lecture
feedback** for teaching materials, **Proposal** for new metrics, guards, weaknesses or cases.
Security issues go through `SECURITY.md`, not the public tracker.
