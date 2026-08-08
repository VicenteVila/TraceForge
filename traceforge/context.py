import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional

from .core import TraceCollector, TraceSpan
from .decorator import (
    _current_parent_id,
    _current_trace_id,
    _get_default_collector,
    _on_new_trace,
    _on_trace_finished,
)


@contextmanager
def span(
    agent: str,
    model: Optional[str] = None,
    tags: Optional[list[str]] = None,
    collector: Optional[TraceCollector] = None,
) -> Generator[TraceSpan, Any, None]:
    _collector = collector or _get_default_collector()
    _tags = tags or []

    inherited_trace_id = _current_trace_id.get()
    is_new_trace = inherited_trace_id is None
    trace_id = inherited_trace_id or str(uuid.uuid4())
    parent_id = _current_parent_id.get()
    span_id = str(uuid.uuid4())

    span = TraceSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        agent=agent,
        model=model,
        tags=_tags,
        started_at=datetime.now(),
    )

    token_trace = _current_trace_id.set(trace_id)
    token_parent = _current_parent_id.set(span_id)
    if is_new_trace:
        _on_new_trace(trace_id)

    try:
        yield span
    except Exception as e:
        span.error = f"{type(e).__name__}: {str(e)}"
        span.status = "error"
        raise
    finally:
        if span.finished_at is None:
            span.close()
        _collector.save(span)
        _current_trace_id.reset(token_trace)
        _current_parent_id.reset(token_parent)
        if is_new_trace:
            _on_trace_finished(trace_id)
