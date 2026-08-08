import contextvars
import functools
import inspect
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from .collector.memory import MemoryCollector
from .core import TraceCollector, TraceSpan, _capture_input, _capture_output

_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_current_parent_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("parent_id", default=None)


def trace(
    agent: str,
    model: Optional[str] = None,
    tags: Optional[list[str]] = None,
    collector: Optional[TraceCollector] = None,
):
    _tags = tags or []

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _collector = collector or _get_default_collector()
                inherited_trace_id = _current_trace_id.get()
                trace_id = inherited_trace_id or str(uuid.uuid4())
                parent_id = _current_parent_id.get()
                span_id = str(uuid.uuid4())

                input_data, input_truncated = _capture_input(args, kwargs)

                span = TraceSpan(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_id=parent_id,
                    agent=agent,
                    model=model,
                    input=input_data,
                    input_truncated=input_truncated,
                    tags=_tags,
                    started_at=datetime.now(),
                )

                token_trace = _current_trace_id.set(trace_id)
                token_parent = _current_parent_id.set(span_id)

                try:
                    result = await func(*args, **kwargs)
                    span.output, span.output_truncated = _capture_output(result)
                    span.status = "ok"
                    return result
                except Exception as e:
                    span.error = f"{type(e).__name__}: {str(e)}"
                    span.status = "error"
                    raise
                finally:
                    span.close()
                    _collector.save(span)
                    _current_trace_id.reset(token_trace)
                    _current_parent_id.reset(token_parent)

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                _collector = collector or _get_default_collector()
                inherited_trace_id = _current_trace_id.get()
                trace_id = inherited_trace_id or str(uuid.uuid4())
                parent_id = _current_parent_id.get()
                span_id = str(uuid.uuid4())

                input_data, input_truncated = _capture_input(args, kwargs)

                span = TraceSpan(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_id=parent_id,
                    agent=agent,
                    model=model,
                    input=input_data,
                    input_truncated=input_truncated,
                    tags=_tags,
                    started_at=datetime.now(),
                )

                token_trace = _current_trace_id.set(trace_id)
                token_parent = _current_parent_id.set(span_id)

                try:
                    result = func(*args, **kwargs)
                    span.output, span.output_truncated = _capture_output(result)
                    span.status = "ok"
                    return result
                except Exception as e:
                    span.error = f"{type(e).__name__}: {str(e)}"
                    span.status = "error"
                    raise
                finally:
                    span.close()
                    _collector.save(span)
                    _current_trace_id.reset(token_trace)
                    _current_parent_id.reset(token_parent)

            return sync_wrapper

    return decorator


_default_collector: Optional[TraceCollector] = None


def _get_default_collector() -> TraceCollector:
    global _default_collector
    if _default_collector is None:
        _default_collector = MemoryCollector()
    return _default_collector


def set_default_collector(collector: TraceCollector) -> None:
    global _default_collector
    _default_collector = collector
