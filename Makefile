# Stockroom agent-evals workshop. Every target runs offline in mock mode unless STOCKROOM_MODE=live.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV            ?= uv
PY            := $(UV) run python
PYTEST        := $(UV) run pytest
# Promptfoo and Marp run from the pinned local install (package.json / package-lock.json, `npm ci`),
# never via an npx download, so `make promptfoo` and `make slides` cannot reach the network.
NODE_BIN      := $(CURDIR)/node_modules/.bin
MCP_PORT      ?= 8765
QUARTO        ?= quarto
REPORTS       := reports
NOTEBOOK_HANDOFF := $(REPORTS)/notebooks_handoff

export STOCKROOM_MODE ?= mock
export DEEPEVAL_TELEMETRY_OPT_OUT := 1
export PROMPTFOO_DISABLE_TELEMETRY := 1
export PROMPTFOO_DISABLE_UPDATE := 1
export PROMPTFOO_PYTHON := $(CURDIR)/.venv/bin/python
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: help setup lint format test test-unit eval node-tools promptfoo thresholds baseline ci \
        build-notebooks notebooks mcp-server mcp-smoke slides validate-data phoenix pins clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install all dependency groups (offline-safe after first run)
	$(UV) python install 3.12
	$(UV) sync --extra phoenix --group dev
	$(PY) -m ipykernel install --user --name stockroom --display-name "Python (stockroom)" >/dev/null
	npm ci --ignore-scripts --no-audit --no-fund   # promptfoo + marp pinned in package-lock.json

lint: ## Ruff lint + format check
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

format: ## Auto-format with ruff
	$(UV) run ruff format src tests scripts
	$(UV) run ruff check --fix src tests scripts

validate-data: ## Validate catalogue, policy docs, golden and calibration datasets
	$(PY) scripts/validate_data.py

test-unit: ## Unit tests only (no golden-set regression)
	$(PYTEST) tests --ignore=tests/test_trajectory_regression.py

test: ## Full pytest suite in mock mode (unit + regression); writes reports/eval_results.json
	mkdir -p $(REPORTS)
	$(PYTEST) tests

eval: ## Golden-set regression suite only
	mkdir -p $(REPORTS)
	$(PYTEST) tests/test_trajectory_regression.py

mcp-server: ## Start the MCP inventory server over streamable HTTP (foreground)
	$(PY) -m stockroom.mock_server.mcp_inventory_server --transport streamable-http --port $(MCP_PORT)

mcp-smoke: ## List tools from a running MCP HTTP server
	$(PY) scripts/mcp_smoke.py --url http://127.0.0.1:$(MCP_PORT)/mcp

node-tools:
	@test -x $(NODE_BIN)/promptfoo -a -x $(NODE_BIN)/marp || \
	  { echo "Promptfoo/Marp are not installed: run 'make setup' (npm ci) once with network access" >&2; exit 1; }

promptfoo: node-tools ## Run the Promptfoo suite (golden slice + red team) via the Python provider
	mkdir -p $(REPORTS)
	rm -f $(REPORTS)/promptfoo_results.json
	# promptfoo exits non-zero when any case fails; the gate script decides, so only a missing results file is fatal here.
	$(NODE_BIN)/promptfoo eval -c promptfooconfig.yaml --no-cache --no-progress-bar \
	  --output $(REPORTS)/promptfoo_results.json < /dev/null || test -s $(REPORTS)/promptfoo_results.json

thresholds: ## Enforce eval_thresholds.yaml against the latest results and write reports/summary.md
	$(PY) scripts/check_thresholds.py --results $(REPORTS)/eval_results.json \
	  --promptfoo $(REPORTS)/promptfoo_results.json --baseline $(REPORTS)/baseline/main.json \
	  --summary $(REPORTS)/summary.md

baseline: eval ## Regenerate the committed main-branch baseline from a clean mock run
	$(PY) scripts/check_thresholds.py --results $(REPORTS)/eval_results.json --write-baseline $(REPORTS)/baseline/main.json

build-notebooks: ## Generate student + solution .ipynb files from notebooks/src
	$(PY) scripts/build_notebooks.py

notebooks: build-notebooks ## Build and execute every notebook in mock mode (solutions in strict mode)
	# A scratch hand-off directory (src/stockroom/handoff.py), emptied first: the solution notebooks never
	# overwrite a participant's reports/participant/, the student pass (nothing solved, nothing saved) runs
	# Day 4 on the reference fallbacks, and the solution pass runs it on what Days 1-3 just saved.
	rm -rf $(NOTEBOOK_HANDOFF)
	STOCKROOM_HANDOFF_DIR=$(CURDIR)/$(NOTEBOOK_HANDOFF) \
	  $(PYTEST) --nbmake --nbmake-timeout=900 notebooks/*.ipynb -p no:cacheprovider
	# Strict mode: an exercise that does not report passing fails its solution notebook.
	STOCKROOM_STRICT_EXERCISES=1 STOCKROOM_HANDOFF_DIR=$(CURDIR)/$(NOTEBOOK_HANDOFF) \
	  $(PYTEST) --nbmake --nbmake-timeout=900 notebooks/solutions/*.ipynb -p no:cacheprovider

slides: node-tools ## Render the Marp deck to HTML
	# stdin is redirected: marp-cli otherwise treats a non-TTY stdin as an extra markdown input and hangs under CI/make.
	$(NODE_BIN)/marp slides/DAY1_MOTIVATIONAL_SLIDES.md -o slides/DAY1_MOTIVATIONAL_SLIDES.html < /dev/null

site: slides ## Render the Quarto website to _site (executes the notebooks in mock mode; needs quarto on PATH)
	QUARTO_PYTHON=$(CURDIR)/.venv/bin/python $(QUARTO) render
	# Quarto writes the executed outputs back into the .ipynb files; regenerate them output-free from notebooks/src.
	$(PY) scripts/build_notebooks.py

site-preview: slides ## Serve the Quarto website locally with live reload (http://localhost:4321)
	QUARTO_PYTHON=$(CURDIR)/.venv/bin/python $(QUARTO) preview

phoenix: ## Launch Arize Phoenix locally (port 6006) for Day 2
	$(UV) run --extra phoenix python -m phoenix.server.main serve

pins: ## Print the resolved versions of the key libraries (used in README)
	$(PY) scripts/print_pins.py

ci: ## The PR gate, step by step (mirrors .github/workflows/agent_eval_ci.yml)
	$(MAKE) lint
	$(MAKE) validate-data
	$(MAKE) test-unit
	mkdir -p $(REPORTS)
	$(PY) -m stockroom.mock_server.mcp_inventory_server --transport streamable-http --port $(MCP_PORT) \
	  > $(REPORTS)/mcp_server.log 2>&1 & echo $$! > $(REPORTS)/.mcp.pid
	for i in 1 2 3 4 5 6 7 8 9 10; do $(MAKE) -s mcp-smoke >/dev/null 2>&1 && break; sleep 1; done; $(MAKE) mcp-smoke
	STOCKROOM_TOOL_TRANSPORT=mcp-http $(MAKE) eval || { kill $$(cat $(REPORTS)/.mcp.pid) 2>/dev/null; exit 1; }
	kill $$(cat $(REPORTS)/.mcp.pid) 2>/dev/null || true
	$(MAKE) promptfoo
	$(MAKE) thresholds
	$(MAKE) notebooks
	$(MAKE) slides
	$(PY) scripts/check_lecture_refs.py
	$(PY) scripts/check_style.py

clean: ## Remove caches, build artefacts and generated reports (keeps a participant's reports/participant/)
	rm -rf .pytest_cache .ruff_cache _site .quarto reports/eval_results.json reports/promptfoo_results.json reports/summary.md
	rm -rf $(NOTEBOOK_HANDOFF)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
