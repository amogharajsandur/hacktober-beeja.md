# One-command workflows. Requires Python 3.11+ and (for the viewer) Node 20+.
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: setup test lint viewer viewer-data demo benchmark mcp clean

setup:            ## create the venv, install Beeja (+ dev/MCP extras) and the viewer deps
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[dev]"
	cd web && npm ci --no-audit --no-fund

lint:
	$(BIN)/ruff check .

test: lint        ## everything CI runs
	$(BIN)/pytest -q
	cd web && npm run build && npm test

viewer-data:      ## regenerate web/public/data from examples/
	$(BIN)/beeja export-web --examples examples --out web/public/data

viewer: viewer-data  ## open the decision dossier at http://localhost:5173
	cd web && npm run dev

benchmark:
	$(BIN)/beeja benchmark --dataset benchmarks --outputs examples

demo:             ## offline demo on the committed real outputs (no model needed)
	$(BIN)/beeja why-not "YAML config" --store decisions
	$(BIN)/beeja check-diff examples/check-diff/related_quillfmt_yaml.patch --store decisions
	$(BIN)/beeja check-diff examples/check-diff/unrelated_docs.patch --store decisions
	$(BIN)/beeja benchmark | head -18

mcp:
	$(BIN)/beeja-mcp --store decisions

clean:
	rm -rf out web/dist .pytest_cache .ruff_cache
