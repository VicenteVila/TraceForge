from datetime import datetime
from typing import Optional

from ..core import TraceCollector, TraceSpan


class MemoryCollector(TraceCollector):
    def __init__(self):
        self._spans: dict[str, TraceSpan] = {}
        self._traces: dict[str, list[str]] = {}
        self._trace_order: list[str] = []

    def save(self, span: TraceSpan) -> None:
        is_new = span.span_id not in self._spans
        self._spans[span.span_id] = span
        if is_new:
            if span.trace_id not in self._traces:
                self._traces[span.trace_id] = []
                self._trace_order.append(span.trace_id)
            self._traces[span.trace_id].append(span.span_id)

        if span.parent_id and span.parent_id in self._spans:
            parent = self._spans[span.parent_id]
            if span.span_id not in parent.children:
                parent.children.append(span.span_id)

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        span_ids = self._traces.get(trace_id, [])
        return [self._spans[sid] for sid in span_ids]

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        return self._spans.get(span_id)

    def list_traces(self, limit: int = 10) -> list[str]:
        return self._trace_order[-limit:]

    def get_last_trace_id(self) -> Optional[str]:
        if not self._trace_order:
            return None
        return self._trace_order[-1]

    def query(
        self,
        trace_id: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        since: Optional[datetime] = None,
    ) -> list[TraceSpan]:
        results: list[TraceSpan] = []

        span_pool: list[TraceSpan] = []
        if trace_id:
            span_pool = self.get_trace(trace_id)
        else:
            span_pool = list(self._spans.values())

        for span in span_pool:
            if agent and span.agent != agent:
                continue
            if status and span.status != status:
                continue
            if min_duration_ms is not None and span.duration_ms < min_duration_ms:
                continue
            if since and span.started_at and span.started_at < since:
                continue
            results.append(span)

        return results

    def clear(self) -> None:
        self._spans.clear()
        self._traces.clear()
        self._trace_order.clear()
