# TraceForge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/cogniteam/traceforge/actions/workflows/test.yml/badge.svg)](https://github.com/cogniteam/traceforge/actions/workflows/test.yml)
[![PyPI version](https://badge.fury.io/py/traceforge.svg)](https://pypi.org/project/traceforge/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Trazabilidad estructurada para pipelines multi-agente con LLMs.

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
  Spans: 1 | Cost: $0.0020 | Errors: 0

planner (llama-3.3-70b) → 2.3s | 1400 tokens | $0.0009 ✓
```

---

## ¿Por qué TraceForge?

Cuando un pipeline multi-agente falla, las preguntas son siempre las mismas:

> *"Mi agente falló en el paso 3 de 7, ¿qué vio, qué pensó, cuánto costó y por qué no se recuperó?"*

Los logs tradicionales no conectan causas. TraceForge enlaza cada paso con un **trace_id persistente** que atraviesa toda la ejecución, preservando la jerarquía de llamadas, el coste por modelo y la latencia de cada nodo.

### Lo que NO es

- ❌ **No** es un sistema de logging general (no compite con structlog o loguru)
- ❌ **No** es un APM tradicional (no compite con Datadog/New Relic)
- ❌ **No** es un reemplazo de OpenTelemetry (puede exportar a OTEL, pero no es su propósito principal)

### Lo que SÍ es

- ✅ Herramienta que entiende que los "pasos" en un pipeline multi-agente son **nodos en un grafo**, no líneas en un log
- ✅ Decorador `@trace()` que captura input/output/error automáticamente
- ✅ Context manager `span()` para código que no es una función
- ✅ Persistencia en SQLite con consultas por agente, estado, duración
- ✅ Cálculo automático de coste por modelo (Gemini, GPT, Claude, Llama, DeepSeek, etc.)
- ✅ CLI con árboles Rich y reportes HTML con Gantt interactivo
- ✅ Exportación a OpenTelemetry

---

## Instalación

```bash
pip install traceforge
```

Con extras:

```bash
pip install traceforge[plotly]   # reportes HTML con Gantt
pip install traceforge[otel]     # exportación OpenTelemetry
pip install traceforge[dev]      # desarrollo (pytest)
```

---

## Quickstart

### 1. Decorador básico

```python
import traceforge

traceforge.configure()

@traceforge.trace(agent="saludo", model="mock-1.0")
def saludar(nombre: str) -> str:
    return f"Hola, {nombre}!"

saludar("Mundo")
traceforge.show(traceforge.get_last_trace_id())
```

### 2. Context manager para bloques

```python
with traceforge.span(agent="developer", model="llama-3.3-70b") as span:
    result = execute_code(code)
    span.set_output(result)
    span.set_tokens(input=1500, output=800)
    if result.error:
        span.set_error(str(result.error))
```

### 3. Pipeline multi-agente

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

### 4. Consultas

```python
# Todas las trazas de un agente
traceforge.query(agent="planner")

# Solo fallos
traceforge.query(status="error")

# Ejecuciones lentas (>5s)
traceforge.query(min_duration_ms=5000)
```

---

## API

| Función | Descripción |
|---|---|
| `configure(collector, db_path)` | Configurar backend (memory, sqlite, otel) |
| `@trace(agent, model, tags)` | Decorador para instrumentar funciones |
| `span(agent, model, tags)` | Context manager para bloques de código |
| `query(trace_id, agent, status, ...)` | Consultar spans con filtros |
| `report(trace_id, format, output)` | Generar reporte HTML/JSON/Markdown |
| `show(trace_id)` | Mostrar árbol en terminal |
| `get_last_trace_id()` | Último trace_id generado |
| `list_traces(limit)` | Listar trace_ids recientes |

---

## CLI

```bash
traceforge list --last 10                    # últimas 10 ejecuciones
traceforge show abc-123                       # árbol de spans
traceforge stats --agent planner --since 7    # métricas por agente
traceforge report abc-123 -o report.html      # reporte HTML con Gantt
traceforge export --format json               # exportar a JSON
traceforge export --format otel --since 7     # exportar a OpenTelemetry
```

---

## Modelos soportados (coste automático)

| Familia | Modelos |
|---|---|
| **Gemini** | 1.5 flash/pro, 2.0 flash/lite, 2.5 flash/pro |
| **OpenAI** | GPT-4o/mini/turbo, o1/mini/preview, o3-mini |
| **Anthropic** | Claude 3 haiku/sonnet/opus, 3.5 sonnet/haiku, 4 sonnet |
| **Meta (Llama)** | 3.1/3.2/3.3/4 (8B a 405B) |
| **DeepSeek** | V3, R1, R1 distill, coder:6.7b |
| **Mistral** | small/medium/large, Codestral |
| **Groq** | llama, mixtral, gemma |
| **Qwen** | 2.5-72b, 2.5-coder-32b |
| **Cohere** | Command R / R+ |

¿Falta un modelo? Abre un issue o añádelo en `traceforge/pricing.py`.

---

## FAQ

### ¿Cómo integro TraceForge con OpenAI?

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

### ¿Funciona con async?

Sí. `@traceforge.trace` detecta automáticamente si la función es async y usa el manejador apropiado.

```python
@traceforge.trace(agent="worker", model="gpt-4o")
async def process(data: str) -> str:
    return await llm_call(data)
```

### ¿Puedo usar mi propio collector?

Sí. Hereda de `TraceCollector` e implementa `save()`, `get_trace()`, `query()`.

```python
from traceforge import TraceCollector

class MyCollector(TraceCollector):
    def save(self, span):
        ...
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

---

## Licencia

MIT

---

## Contribuir

1. Fork el repo
2. Crea una rama (`git checkout -b feature/nueva-funcionalidad`)
3. Commit (`git commit -am 'Añade nueva funcionalidad'`)
4. Push (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

Los tests deben pasar y el código debe ser formateado con ruff.
