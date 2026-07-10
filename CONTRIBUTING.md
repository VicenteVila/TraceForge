# Contributing to TraceForge

Thank you for considering contributing! We welcome bug reports, feature requests, documentation improvements, and code changes.

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How to contribute

### 1. Reporting bugs

Open a [GitHub Issue](https://github.com/VicenteVila/TraceForge/issues/new?template=bug_report.md) with:
- A clear title and description
- Steps to reproduce (code snippet preferred)
- Expected vs actual behavior
- Python version and OS

### 2. Suggesting features

Open a [feature request](https://github.com/VicenteVila/TraceForge/issues/new?template=feature_request.md) with:
- A clear use case
- Why it's useful for multi-agent tracing
- API sketch (if applicable)

### 3. Pull requests

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run the tests: `pytest`
5. Run the linter: `ruff check .`
6. Commit: `git commit -am 'Add my feature'`
7. Push: `git push origin feature/my-feature`
8. Open a Pull Request

### Development setup

```bash
git clone https://github.com/VicenteVila/TraceForge.git
cd TraceForge
pip install -e ".[dev]"
```

### Style guide

- Line length: 120
- Formatter: [ruff](https://github.com/astral-sh/ruff)
- Import sorting: ruff (isort-compatible)
- Type hints: required for all public APIs
- No commented-out code

### Testing

- All new features must include tests
- Async tests use `pytest-asyncio` (auto-detected)
- Run full suite before opening a PR: `pytest`

## Project structure

```
traceforge/               # Library package
  __init__.py             # Public API
  core.py                 # TraceSpan model
  decorator.py            # @trace decorator
  context.py              # span() context manager
  collector/              # Storage backends
    memory.py
    sqlite.py
    otel.py
  pricing.py              # Model cost table
  report.py               # HTML/MD/JSON reporters
  cli.py                  # Typer CLI
tests/                    # Test suite
examples/                 # Runnable examples
```
