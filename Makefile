# One-command workflows. Requires Python 3.11+ and (for the viewer) Node 20+.
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: setup help test lint viewer viewer-data demo extract benchmark mcp clean

say = @printf '\n\033[1;96m━━ %s ━━\033[0m\n' "$(1)"

setup:            ## create the venv, install Beeja (+ dev/MCP extras) and the viewer deps
	$(call say,1/3 Python environment)
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[dev]"
	$(call say,2/3 Web viewer dependencies)
	cd web && npm ci --no-audit --no-fund
	$(call say,3/3 Model configuration)
	@test -f .env && echo ".env already exists - left untouched" || (cp .env.example .env && echo "created .env from .env.example - edit it to choose your model")
	@printf '\n\033[1;92m✔ ready.\033[0m  try: make demo · make viewer · make help\n'

help:             ## list the make targets
	@grep -E '^[a-z-]+:.*##' Makefile | sed -E 's/:.*## /\t/' | expand -t 18

lint:
	$(BIN)/ruff check .

test: lint        ## everything CI runs
	$(BIN)/pytest -q
	cd web && npm run build && npm test

viewer-data:      ## regenerate web/public/data from examples/
	$(BIN)/beeja export-web --examples examples --out web/public/data

viewer: viewer-data  ## open the decision dossier at http://localhost:5173
	cd web && npm run dev

benchmark:        ## score committed outputs against the withheld PEP ground truth
	@FORCE_COLOR=1 $(BIN)/beeja benchmark --dataset benchmarks --outputs examples

demo:             ## offline demo on the committed real outputs (no model needed)
	@FORCE_COLOR=1 $(BIN)/beeja why-not "YAML config" --store decisions
	@FORCE_COLOR=1 $(BIN)/beeja search "single shared buffer" --store decisions --limit 3
	@FORCE_COLOR=1 $(BIN)/beeja check-diff examples/check-diff/related_quillfmt_yaml.patch --store decisions
	@FORCE_COLOR=1 $(BIN)/beeja check-diff examples/check-diff/unrelated_docs.patch --store decisions
	@FORCE_COLOR=1 $(BIN)/beeja benchmark

extract:          ## run the model configured in .env on FILE=... (default: the synthetic discussion)
	@FORCE_COLOR=1 $(BIN)/beeja extract -i $(or $(FILE),benchmarks/synthetic-config-format/input/discussion.txt) -o out/decision.json

mcp:
	$(BIN)/beeja-mcp --store decisions

clean:
	rm -rf out web/dist .pytest_cache .ruff_cache
