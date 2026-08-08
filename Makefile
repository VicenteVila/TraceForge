.PHONY: install install-dev lint format check test coverage build clean

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev,plotly,otel,postgres,clickhouse]"

lint:
	$(BIN)/ruff check traceforge/ tests/

format:
	$(BIN)/ruff format traceforge/ tests/
	$(BIN)/ruff check --fix traceforge/ tests/

check:
	$(BIN)/ruff check traceforge/ tests/
	$(BIN)/ruff format --check traceforge/ tests/

test:
	$(BIN)/pytest -q

coverage:
	$(BIN)/pytest --cov=traceforge --cov-report=term-missing

build:
	$(BIN)/python -m build

clean:
	rm -rf build dist *.egg-info .pytest_cache .coverage coverage.xml
