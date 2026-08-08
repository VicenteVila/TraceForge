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
- ✅ Streaming metrics (TTFT, token throughput, per-chunk latency)
- ✅ Automatic PII masking (emails, phones, credit cards, IPs, + optional NER)
- ✅ One-line `init()` for auto-instrumentation of OpenAI/Anthropic/LangChain/LlamaIndex
- ✅ Live price refresh from LiteLLM with local cache

---

## Installation

```bash
pip install git+https://github.com/VicenteVila/TraceForge.git
```

With extras:

```bash
pip install "traceforge[plotly] @ git+https://github.com/VicenteVila/TraceForge.git"   # HTML reports with Gantt charts
pip install "traceforge[otel] @ git+https://github.com/VicenteVila/TraceForge.git"     # OpenTelemetry export
pip install "traceforge[postgres] @ git+https://github.com/VicenteVila/TraceForge.git" # PostgreSQL backend
pip install "traceforge[clickhouse] @ git+https://github.com/VicenteVila/TraceForge.git" # ClickHouse backend
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

### 0. One-line activation

Activate tracing, collectors and auto-instrumentation in a single call:

```python
import traceforge

traceforge.init(auto_instrument=["openai", "langchain"])
```

This configures the default collector, enables automatic PII masking, and
monkey-patches the installed SDKs you listed. The rest of the quickstart uses
the explicit building blocks.

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
| `init(auto_instrument, collector, db_path, dsn, ...)` | One-line activation: configure + instrument providers |
| `configure(collector, db_path, dsn, max_input_len, redact_pii, ...)` | Set backend and capture limits (memory, sqlite, postgres, clickhouse, otel) |
| `@trace(agent, model, tags)` | Decorate functions for automatic tracing |
| `span(agent, model, tags)` | Context manager for inline code blocks |
| `query(trace_id, agent, status, ...)` | Search spans with filters |
| `report(trace_id, format, output)` | Generate HTML / JSON / Markdown report |
| `show(trace_id)` | Print trace tree to terminal |
| `get_last_trace_id()` | Return the last generated trace_id |
| `list_traces(limit)` | List recent trace_ids |
| `set_truncation_limits(max_input_len, max_output_len, max_list_items)` | Adjust capture limits at runtime (0 disables) |
| `instrument()` | Enable auto-instrumentation for installed SDKs (returns `{provider: bool}`) |

---

## CLI

```bash
traceforge list --last 10                          # last 10 traces
traceforge list --json                             # machine-readable
traceforge show abc-123                            # span tree
traceforge show abc-123 --json                     # spans as JSON
traceforge stats --agent planner --since 7         # metrics by agent
traceforge stats --json
traceforge query --agent planner --status error --min-duration 500
traceforge query --since 7 --json
traceforge report abc-123 -o report.html           # HTML report with Gantt
traceforge export --format json                    # export to JSON
traceforge export --format otel --since 7          # export to OpenTelemetry
traceforge clear --yes                             # wipe all traces
traceforge dashboard --port 8080                   # web dashboard (SPA)
```

Point the CLI at any backend (memory, sqlite, postgres, clickhouse):

```bash
traceforge --collector sqlite --db-path prod.db stats
traceforge --collector postgres --dsn "postgresql://user:pass@host:5432/db" query --status error
traceforge --collector clickhouse --dsn "http://user:pass@localhost:8123/db" list
```

If only `--dsn` is given, the backend is inferred from the URL (`http(s)` → clickhouse, otherwise postgres).

---

## Web dashboard

A zero-dependency web dashboard (single-file SPA over a small stdlib HTTP API)
lets you inspect traces, per-agent stats and filtered queries in the browser:

```bash
traceforge dashboard --port 8080        # or: python -m traceforge.dashboard
# open http://127.0.0.1:8080
```

Or from Python against any collector:

```python
import traceforge

traceforge.init(collector="postgres", dsn="postgresql://...", auto_instrument=["openai"])
traceforge.dashboard(host="0.0.0.0", port=8080)
```

The backend exposes a JSON API (`/api/health`, `/api/traces`, `/api/trace/<id>`,
`/api/stats`, `/api/query`) that scripts can consume too.

---

## Evals

`traceforge.evals` scores captured spans — pure Python, no extra dependencies
for the built-in checks, with optional LLM-as-judge upgrades:

```python
import traceforge
from traceforge.evals import openai_judge, summary

traceforge.init(auto_instrument=["openai"])

results = traceforge.run_evals(judge=openai_judge())     # runs over saved spans
print(summary(results))
# {'factuality': {'avg_score': 0.92, 'pass_rate': 1.0, 'count': 40}, ...}
```

- **Factuality** — ROUGE-L F-score against a ground truth: pass `references={span_id_or_trace_id: truth}`.
- **Toxicity** — a built-in lexicon by default; upgraded to an LLM judge when `judge=` is given (scores are normalized to *higher = better*, so a high toxicity score means clean/safe).
- **LLM-as-judge** — any callable `prompt -> response`; helpers `openai_judge()`, `anthropic_judge()` are provided.

Per-span control:

```python
from traceforge.evals import evaluate_span, run_evals

results = run_evals(collector, span_ids=[sid], judge=openai_judge(), references={sid: "ground truth"})
```

---

## A/B testing prompts

`traceforge.abtest` runs an instrumented function over prompt variants and
picks a winner by quality (when a judge/references are given), cost and latency:

```python
from traceforge.abtest import compare_prompts
from traceforge.evals import openai_judge
import traceforge

@traceforge.trace(agent="writer")
def answer(prompt, sample):
    return client.chat.completions.create(model="gpt-4o-mini", messages=[...])  # auto-instrumented

result = compare_prompts(
    answer,
    {"concise": "Answer in one sentence.", "detailed": "Answer in three sentences."},
    samples=["What is AI?", "Explain entropy."],
    references=["A clear definition of AI", "Entropy is disorder."],
    judge=openai_judge(),
)
print(result.winner, result.reason)      # e.g. 'concise  eval 0.833, $0.0001/run, 42ms avg'
print(result)                            # full per-variant metrics
```

Variants are compared on error rate, average duration, tokens, cost and
(averaged) eval score; the winner never has 100% errors.

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

Missing a model? Open an issue, add it in `traceforge/pricing.py`, or refresh prices from LiteLLM:

```python
import traceforge

traceforge.refresh_prices()                       # fetch latest catalog from LiteLLM
traceforge.refresh_prices(url="https://your-api/prices.json")  # or your own pricing API
```

Refreshed prices are cached locally (default `~/.cache/traceforge/pricing_cache.json`)
and merged into cost calculations automatically on the next run. Pass `cache_path=`
to override the location.

---

## Data capture limits

Inputs and outputs are captured with size limits to keep traces lean, but data loss is **never silent**:

- A `RuntimeWarning` is emitted whenever truncation happens.
- Every span records `input_truncated` / `output_truncated` flags, persisted in SQLite and shown as a `⚠ truncated` marker in the CLI tree and HTML reports.
- Limits are configurable and can be disabled entirely:

```python
import traceforge

# Defaults: input 2000 chars, output 5000 chars, 10 list items
traceforge.configure(collector="sqlite", db_path="traces.db",
                     max_input_len=10_000, max_output_len=20_000,
                     max_list_items=50)

# Or at runtime (0 = unlimited)
traceforge.set_truncation_limits(max_input_len=0, max_output_len=0)
```

---

## PII masking

Captured inputs and outputs are masked by default so emails, phone numbers,
credit cards, SSNs and IPv4 addresses never land in your traces:

```python
import traceforge

traceforge.configure(redact_pii=False)      # opt out
traceforge.set_pii_masker(enabled=True)     # re-enable at runtime
```

- Masking is applied after truncation, recursively over args, kwargs, messages and outputs.
- A match is replaced by its label (`<email>`, `<phone>`, `<credit_card>`, ...), keeping the structure intact.
- **Optional NER**: `traceforge.set_pii_masker(use_ner=True)` enables a lightweight spaCy pass (`en_core_web_sm`) to catch names and other entities regexes miss.
- Custom patterns: `traceforge.set_pii_masker(patterns={"internal_id": r"ID-\d{5}"})`.

---

## Production backends (PostgreSQL / ClickHouse)

For multi-instance or high-volume deployments, SQLite can be swapped for a real
database. Both are self-hosted, open-source and free — the repo ships a
`docker-compose.yml` to run them locally with zero cost:

```bash
docker compose up -d
```

Then point TraceForge at them:

```python
import traceforge

# PostgreSQL (psycopg)
traceforge.configure(collector="postgres", dsn="postgresql://traceforge:traceforge@localhost:5432/traceforge")

# ClickHouse (clickhouse-connect, HTTP)
traceforge.configure(collector="clickhouse", dsn="http://localhost:8123/traceforge")
```

- **PostgreSQL** mirrors the SQLite schema (upsert + atomic parent-child linking) and is the drop-in choice for teams.
- **ClickHouse** is append-only: spans live in a `ReplacingMergeTree` and the parent-child tree is reconstructed at read time, which keeps writes cheap at high volume.
- Both implement the full `TraceCollector` interface, so `query()`, `report()`, `show()` and the CLI work unchanged.
- Install the drivers per backend: `pip install traceforge[postgres]` or `traceforge[clickhouse]` (the core stays dependency-free).
- Integration tests run only when a server is available:

```bash
TRACEFORGE_TEST_PG_DSN="postgresql://traceforge:traceforge@localhost:5432/traceforge" pytest tests/test_integration_postgres.py
TRACEFORGE_TEST_CH_URL="http://localhost:8123/traceforge" pytest tests/test_integration_clickhouse.py
```

---

## Auto-instrumentation

Instead of decorating every call, let TraceForge hook into your LLM SDK. Calls made through the patched API produce spans automatically, including model and token usage:

```python
import traceforge

# auto_trace instruments whatever is installed (openai, anthropic) at configure() time
traceforge.configure(collector="sqlite", db_path="traces.db", auto_trace=True)
```

Or instrument on demand:

```python
from traceforge.auto import instrument

results = instrument()          # {openai: True, anthropic: False} if only openai is installed
```

It also supports scoping to a single client and a custom agent label:

```python
from openai import OpenAI
from traceforge.auto import instrument_openai

client = OpenAI()
instrument_openai(client=client, agent="openai_call")
```

Errors raised by the SDK are captured on the span (status `error`) and re-raised to your code.

The **full prompt** is captured on `span.input` — the entire `messages` list (system + user turns) or `prompt` sent to the SDK — not just function arguments, and it goes through the same truncation and PII masking as any other input.

**Framework callbacks** are provided for LangChain/LangGraph (`TraceForgeLangChainHandler`) and LlamaIndex (`TraceForgeLlamaIndexHandler`) `CallbackHandler` interfaces. They can also be registered globally from `init()` or `instrument(providers=["langchain", "llamaindex"])`.

### Streaming

Streaming calls (`stream=True`) are traced transparently. The span stays open while you consume the stream and records the metrics you need to reason about perceived latency:

```python
client = OpenAI()
instrument_openai(client=client)

stream = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
    stream_options={"include_usage": True},   # gives exact token counts
)
for chunk in stream:
    print(chunk.choices[0].delta.content, end="")  # chunks are untouched
```

When the stream finishes, the span contains:

- **`ttft_ms`** — time-to-first-token (ms from request to first chunk).
- **`chunk_offsets_ms`** — cumulative arrival offset of each chunk (per-token latency).
- **`stream_chunks`** — number of chunks received.
- **`throughput_tps`** — output tokens per second, derived from duration.
- **`tokens_input` / `tokens_output`** — exact when the SDK reports usage (e.g. OpenAI `stream_options={"include_usage": True}`, Anthropic `message_delta`); otherwise output tokens are estimated.

`with client.chat.completions.create(...)` and `async for` (AsyncOpenAI/AsyncAnthropic) work too; errors raised mid-stream mark the span as `error`.

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
pytest                 # unit tests + coverage gate (>=80%), fails under threshold
ruff check .           # zero errors
ruff format --check .  # formatting
make check             # lint + format in one step
make coverage          # detailed coverage report
```

Quality gates are enforced in CI (`.github/workflows`): ruff lint + format on
every push/PR, a unit-test matrix (Python 3.10–3.13 × Linux/macOS/Windows) with
a coverage gate, and an auto-publish to PyPI on version tags. For local hygiene
there's a `Makefile` and a `.pre-commit-config.yaml`:

```bash
make install-dev
pre-commit install       # ruff + basic hooks on every commit
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
