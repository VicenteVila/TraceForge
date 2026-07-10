import atexit
from datetime import datetime
from typing import Optional

from .collector.memory import MemoryCollector
from .context import span as _span_context
from .core import TraceSpan
from .decorator import set_default_collector, trace

_collector: MemoryCollector = MemoryCollector()
set_default_collector(_collector)


def _close_collector() -> None:
    global _collector
    if hasattr(_collector, "close"):
        _collector.close()


atexit.register(_close_collector)


def configure(
    collector: str = "memory",
    db_path: Optional[str] = None,
    auto_trace: bool = False,
) -> None:
    global _collector
    if hasattr(_collector, "close"):
        _collector.close()

    if collector == "sqlite":
        from .collector.sqlite import SQLiteCollector
        _collector = SQLiteCollector(db_path or "traces.db")
    elif collector == "memory":
        _collector = MemoryCollector()
    elif collector == "otel":
        try:
            from .collector.otel import OTELCollector
            _collector = OTELCollector()
        except ImportError:
            raise ImportError("OpenTelemetry support requires opentelemetry-api")
    else:
        raise ValueError(f"Unknown collector: {collector}")
    set_default_collector(_collector)


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
    from .report import generate_report
    return generate_report(trace_id, format=format, output=output, collector=_collector)


def show(
    trace_id: str,
    format: str = "tree",
) -> None:
    from .cli import show_trace
    show_trace(trace_id, tree=(format == "tree"), collector=_collector)


def get_last_trace_id() -> Optional[str]:
    return _collector.get_last_trace_id()


def list_traces(limit: int = 10) -> list[str]:
    return _collector.list_traces(limit=limit)


span = _span_context

__all__ = [
    "configure",
    "trace",
    "span",
    "query",
    "report",
    "show",
    "get_last_trace_id",
    "list_traces",
    "TraceSpan",
]
