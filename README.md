# TraceForge

[![CI](https://img.shields.io/github/actions/workflow/status/VicenteVila/TraceForge/test.yml?branch=main&label=CI&logo=github)](https://github.com/VicenteVila/TraceForge/actions/workflows/test.yml)
[![Lint](https://img.shields.io/github/actions/workflow/status/VicenteVila/TraceForge/lint.yml?branch=main&label=lint&logo=github)](https://github.com/VicenteVila/TraceForge/actions/workflows/lint.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?logo=python)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Structured tracing for multi-agent LLM pipelines.

```python
import traceforge

traceforge.configure(collector="sqlite", db_path="traces.db")

@traceforge.trace(agent="planner", model="llama-3.3-70b")
def generate_plan(task: str) -> list:
    return [{"step": 1, "action": "analyze"}]

result = generate_plan("Build a landing page")
traceforge.show(traceforge.get_last_trace_id())
```

```
Trace: a1b2c3d4-...
  Spans: 1 | Duration: 2300ms | Tokens: 1400 | Cost: $0.0009 | Errors: 0

planner (llama-3.3-70b) → 2.3s | 1400 tokens | $0.0009 ✓
```

---

## Why TraceForge?

When a multi-agent pipeline fails, the questions are always the same:

> *"My agent failed at step 3 of 7 — what did it see, what did it think, how much did it cost, and why didn't it recover?"*

Traditional logs don't connect causes. TraceForge links every step with a **persistent trace_id** that flows through the entire execution, preserving call hierarchy, per-model cost, and latency at every node.

### What it is NOT

- ❌ A general-purpose logging system (not competing with structlog or loguru)
- ❌ A traditional APM (not competing with Datadog/New Relic)
- ❌ An OpenTelemetry replacement (can export to OTEL, but that's not its primary purpose)

### What it IS

- ✅ A tool that understands pipeline steps are **nodes in a graph**, not lines in a log
- ✅ `@trace()` decorator that captures input/output/error automatically
- ✅ `span()` context manager for code that isn't a function
- ✅ SQLite persistence with queries by agent, status, duration
- ✅ Automatic cost calculation per model (Gemini, GPT, Claude, Llama, DeepSeek, 200+)
- ✅ CLI with Rich tree output and HTML reports (Markdown/JSON also supported)
- ✅ OpenTelemetry export
- ✅ Async support with contextvars for concurrent traces

---

## Installation

```bash
pip install git+https://github.com/VicenteVila/TraceForge.git
```

With extras:

```bash
pip install "traceforge[plotly] @ git+https://github.com/VicenteVila/TraceForge.git"   # HTML reports with Gantt charts
pip install "traceforge[otel] @ git+https://github.com/VicenteVila/TraceForge.git"     # OpenTelemetry export
pip install "traceforge[dev] @ git+https://github.com/VicenteVila/TraceForge.git"      # development (pytest, ruff)
```

From source:

```bash
git clone https://github.com/VicenteVila/TraceForge.git
cd TraceForge
pip install -e .
```

---

## Quickstart

### 1. Basic decorator

```python
import traceforge

traceforge.configure()

@traceforge.trace(agent="greeter", model="mock-1.0")
def greet(name: str) -> str:
    return f"Hello, {name}!"

greet("TraceForge")
traceforge.show(traceforge.get_last_trace_id())
```

### 2. Context manager for blocks

```python
with traceforge.span(agent="developer", model="llama-3.3-70b") as span:
    result = execute_code(code)
    span.set_output(result)
    span.set_tokens(input=1500, output=800)
    if result.error:
        span.set_error(str(result.error))
```

### 3. Multi-agent pipeline

```python
@traceforge.trace(agent="orchestrator", model=None)
def run_pipeline(task: str):
    manifest = scoping.classify(task)       # @trace
    plan = planner.generate(manifest)       # @trace
    artifacts = developer.execute(plan)     # @trace
    return artifacts

result = run_pipeline("Build a landing page")
traceforge.report(traceforge.get_last_trace_id(), output="report.html")
```

### 4. Queries

```python
# All traces for an agent
traceforge.query(agent="planner")

# Only failures
traceforge.query(status="error")

# Slow executions (>5s)
traceforge.query(min_duration_ms=5000)
```

---

## Examples

The [`examples/`](examples/) directory contains ready-to-run scripts:

| Example | What it shows |
|---|---|
| [`basic_usage.py`](examples/basic_usage.py) | Minimal pipeline with decorators |
| [`multi_agent_pipeline.py`](examples/multi_agent_pipeline.py) | Orchestrator with error recovery |
| [`openai_integration.py`](examples/openai_integration.py) | OpenAI call instrumentation |
| [`fastapi_integration.py`](examples/fastapi_integration.py) | FastAPI REST endpoints with tracing |
| [`async_multi_agent.py`](examples/async_multi_agent.py) | Concurrent async agents with `asyncio.gather` |

---

## API

| Function | Description |
|---|---|
| `configure(collector, db_path)` | Set backend (memory, sqlite, otel) |
| `@trace(agent, model, tags)` | Decorate functions for automatic tracing |
| `span(agent, model, tags)` | Context manager for inline code blocks |
| `query(trace_id, agent, status, ...)` | Search spans with filters |
| `report(trace_id, format, output)` | Generate HTML / JSON / Markdown report |
| `show(trace_id)` | Print trace tree to terminal |
| `get_last_trace_id()` | Return the last generated trace_id |
| `list_traces(limit)` | List recent trace_ids |

---

## CLI

```bash
traceforge list --last 10                    # last 10 traces
traceforge show abc-123                       # span tree
traceforge stats --agent planner --since 7    # metrics by agent
traceforge report abc-123 -o report.html      # HTML report with Gantt
traceforge export --format json               # export to JSON
traceforge export --format otel --since 7     # export to OpenTelemetry
```

---

## Supported models (automatic cost)

| Family | Models |
|---|---|
| **Gemini** | 1.5 flash/pro, 2.0 flash/lite, 2.5 flash/pro |
| **OpenAI** | GPT-4o/mini/turbo, o1/mini/preview, o3-mini |
| **Anthropic** | Claude 3 haiku/sonnet/opus, 3.5 sonnet/haiku, 4 sonnet |
| **Meta (Llama)** | 3.1/3.2/3.3/4 (8B to 405B) |
| **DeepSeek** | V3, R1, R1 distill, coder:6.7b |
| **Mistral** | small/medium/large, Codestral |
| **Groq** | llama, mixtral, gemma |
| **Qwen** | 2.5-72b, 2.5-coder-32b |
| **Cohere** | Command R / R+ |

Missing a model? Open an issue or add it in `traceforge/pricing.py`.

---

## FAQ

### How do I integrate TraceForge with OpenAI?

```python
import traceforge
from openai import OpenAI

client = OpenAI()

@traceforge.trace(agent="openai_call", model="gpt-4o")
def ask_llm(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
```

See [`examples/openai_integration.py`](examples/openai_integration.py) for a runnable version.

### Does it work with async?

Yes. `@trace` detects async functions automatically and uses the appropriate handler. Context variables are properly isolated per-task.

```python
@traceforge.trace(agent="worker", model="gpt-4o")
async def process(data: str) -> str:
    return await llm_call(data)
```

See [`examples/async_multi_agent.py`](examples/async_multi_agent.py) for concurrent async pipelines with `asyncio.gather`.

### Can I use my own collector?

Yes. Subclass `TraceCollector` and implement `save()`, `get_trace()`, `query()`.

```python
from traceforge import TraceCollector

class MyCollector(TraceCollector):
    def save(self, span): ...
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest          # 68 tests
ruff check .    # zero errors
```

---

## License

MIT

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

Quick summary:
1. Fork the repo
2. Create a branch (`git checkout -b feature/amazing-feature`)
3. Commit (`git commit -am 'Add amazing feature'`)
4. Push (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Tests must pass and code must be ruff-clean.
