.PHONY: install api test lint format typecheck check dashboard dashboard-build

install:            ## Install Python + dashboard dependencies and the Chromium browser
	uv sync
	uv run playwright install chromium
	cd dashboard && npm install

api:                ## Run the operator API
	uv run company-operator

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
