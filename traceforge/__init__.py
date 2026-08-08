import atexit
from datetime import datetime
from typing import Optional

from .collector.memory import MemoryCollector
from .context import span as _span_context
from .core import TraceCollector, TraceSpan, set_truncation_limits
from .decorator import set_default_collector, trace
from .redact import set_pii_masker

_collector: TraceCollector = MemoryCollector()
set_default_collector(_collector)


def _close_collector() -> None:
    global _collector
    if hasattr(_collector, "close"):
        _collector.close()


atexit.register(_close_collector)


def configure(
    collector: str = "memory",
    db_path: Optional[str] = None,
    dsn: Optional[str] = None,
    auto_trace: bool = False,
    redact_pii: bool = True,
    max_input_len: Optional[int] = None,
    max_output_len: Optional[int] = None,
    max_list_items: Optional[int] = None,
) -> None:
    global _collector
    if max_input_len is not None or max_output_len is not None or max_list_items is not None:
        set_truncation_limits(
            max_input_len=max_input_len,
            max_output_len=max_output_len,
            max_list_items=max_list_items,
        )
    set_pii_masker(enabled=redact_pii)

    if auto_trace:
        from .auto import instrument

        instrument()

    if hasattr(_collector, "close"):
        _collector.close()

    if collector == "sqlite":
        from .collector.sqlite import SQLiteCollector

        _collector = SQLiteCollector(db_path or "traces.db")
    elif collector == "memory":
        _collector = MemoryCollector()
    elif collector == "postgres":
        from .collector.postgres import PostgresCollector

        _collector = PostgresCollector(dsn or db_path or "postgresql://localhost/traceforge")
    elif collector == "clickhouse":
        from .collector.clickhouse import ClickHouseCollector

        _collector = ClickHouseCollector(dsn or db_path or "http://localhost:8123/default")
    elif collector == "otel":
        try:
            from .collector.otel import OTELCollector

            _collector = OTELCollector()
        except ImportError:
            raise ImportError("OpenTelemetry support requires opentelemetry-api")
    else:
        raise ValueError(f"Unknown collector: {collector}")
    set_default_collector(_collector)


def init(
    collector: str = "memory",
    db_path: Optional[str] = None,
    dsn: Optional[str] = None,
    auto_instrument: Optional[list[str]] = None,
    redact_pii: bool = True,
    max_input_len: Optional[int] = None,
    max_output_len: Optional[int] = None,
    max_list_items: Optional[int] = None,
) -> dict[str, bool]:
    """One-line activation: configure the collector and instrument providers.

    Example::

        import traceforge
        traceforge.init(auto_instrument=["openai", "langchain"])
        traceforge.init(collector="postgres", dsn="postgresql://user:pass@db:5432/tf")

    Returns ``{provider: patched_or_not}`` for the requested providers.
    """
    configure(
        collector=collector,
        db_path=db_path,
        dsn=dsn,
        redact_pii=redact_pii,
        max_input_len=max_input_len,
        max_output_len=max_output_len,
        max_list_items=max_list_items,
    )
    if auto_instrument:
        from .auto import instrument as _instrument

        return _instrument(collector=_collector, providers=auto_instrument)
    return {}


def query(
    trace_id: Optional[str] = None,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    min_duration_ms: Optional[int] = None,
    since: Optional[datetime] = None,
) -> list[TraceSpan]:
    return _collector.query(
        trace_id=trace_id,
        agent=agent,
        status=status,
        min_duration_ms=min_duration_ms,
        since=since,
    )


def report(
    trace_id: str,
    format: str = "html",
    output: Optional[str] = None,
) -> str:
    from .reporting import generate_report

    return generate_report(trace_id, format=format, output=output, collector=_collector)


def show(
    trace_id: str,
    format: str = "tree",
) -> None:
    if format == "tree":
        from .cli import show_trace

        show_trace(trace_id, collector=_collector)
    elif format == "json":
        from .reporting import generate_report

        print(generate_report(trace_id, format="json", collector=_collector))
    else:
        raise ValueError(f"Unsupported format: {format}. Supported: tree, json")


def get_last_trace_id() -> Optional[str]:
    return _collector.get_last_trace_id()


def refresh_prices(*args, **kwargs):
    from .pricing import refresh_prices as _refresh

    return _refresh(*args, **kwargs)


def list_traces(limit: int = 10) -> list[str]:
    return _collector.list_traces(limit=limit)


span = _span_context


def instrument(
    collector: Optional[TraceCollector] = None,
    providers: Optional[list[str]] = None,
) -> dict[str, bool]:
    from .auto import instrument as _instrument

    return _instrument(collector=collector, providers=providers)


def run_evals(*args, **kwargs):
    from .evals import run_evals as _run_evals

    return _run_evals(_collector, *args, **kwargs)


def compare_prompts(*args, **kwargs):
    from .abtest import compare_prompts as _compare

    return _compare(*args, **kwargs)


def dashboard(*args, **kwargs):
    from .dashboard import run_dashboard as _dashboard

    return _dashboard(_collector, *args, **kwargs)


__all__ = [
    "configure",
    "init",
    "trace",
    "span",
    "instrument",
    "query",
    "report",
    "show",
    "get_last_trace_id",
    "list_traces",
    "set_truncation_limits",
    "set_pii_masker",
    "refresh_prices",
    "run_evals",
    "compare_prompts",
    "dashboard",
    "TraceSpan",
]
