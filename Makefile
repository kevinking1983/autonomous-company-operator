.PHONY: install api run worker eval sandbox sandbox-reset test lint format typecheck check dashboard dashboard-build

install:            ## Install Python + dashboard dependencies and the Chromium browser
	uv sync
	uv run playwright install chromium
	cd dashboard && npm install

api:                ## Run the operator API and the built dashboard on http://127.0.0.1:8000
	uv run company-operator

run:                ## Resolve one ticket end to end with a fresh sandbox: make run TICKET=TKT-1001
	uv run operator-run --ticket $(or $(TICKET),TKT-1001) --sandbox

worker:             ## Work through the task queue (add tasks with: uv run operator-run --enqueue --ticket TKT-1001)
	uv run operator-worker

eval:               ## Score the operator on known scenarios: make eval SUITE=smoke (smoke, core, reliability)
	uv run operator-eval --suite $(or $(SUITE),smoke)

sandbox:            ## Run the QuickBite sandbox (fresh world) on http://127.0.0.1:8100
	uv run quickbite-sandbox

sandbox-reset:      ## Reset the running sandbox to its seeded state and clear all faults
	curl -fsS -X POST -H "X-Control-Key: $${QUICKBITE_CONTROL_KEY:-sandbox-control}" http://127.0.0.1:8100/_control/reset

test:               ## Run the test suite
	uv run pytest

lint:               ## Lint Python code
	uv run ruff check .
	uv run ruff format --check .

format:             ## Auto-format Python code
	uv run ruff check --fix .
	uv run ruff format .

typecheck:          ## Type-check Python code
	uv run mypy

check: lint typecheck test   ## Everything CI runs for Python

dashboard:          ## Run the dashboard dev server
	cd dashboard && npm run dev

dashboard-build:    ## Type-check and build the dashboard
	cd dashboard && npm run build
