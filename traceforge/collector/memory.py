import threading
from datetime import datetime
from typing import Optional

from ..core import TraceCollector, TraceSpan, _metadata_contains


class MemoryCollector(TraceCollector):
    def __init__(self):
        self._lock = threading.RLock()
        self._spans: dict[str, TraceSpan] = {}
        self._traces: dict[str, list[str]] = {}
        self._trace_order: list[str] = []
        self._pending_children: dict[str, list[str]] = {}

    def save(self, span: TraceSpan) -> None:
        with self._lock:
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
            elif span.parent_id:
                self._pending_children.setdefault(span.parent_id, [])
                if span.span_id not in self._pending_children[span.parent_id]:
                    self._pending_children[span.parent_id].append(span.span_id)

            pending = self._pending_children.pop(span.span_id, [])
            for child_id in pending:
                if child_id in self._spans and child_id not in span.children:
                    span.children.append(child_id)

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        with self._lock:
            span_ids = self._traces.get(trace_id, [])
            return [self._spans[sid] for sid in span_ids]

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        with self._lock:
            return self._spans.get(span_id)

    def list_traces(self, limit: int = 10, offset: int = 0) -> list[str]:
        with self._lock:
            end = len(self._trace_order) - offset
            if end <= 0:
                return []
            start = max(0, end - limit) if limit > 0 else 0
            return self._trace_order[start:end]

    def get_last_trace_id(self) -> Optional[str]:
        with self._lock:
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
        metadata: Optional[dict] = None,
    ) -> list[TraceSpan]:
        results: list[TraceSpan] = []

        span_pool: list[TraceSpan] = []
        with self._lock:
            if trace_id:
                span_ids = self._traces.get(trace_id, [])
                span_pool = [self._spans[sid] for sid in span_ids]
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
            if metadata and not _metadata_contains(span.metadata or {}, metadata):
                continue
            results.append(span)

        return results

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()
            self._traces.clear()
            self._trace_order.clear()
            self._pending_children.clear()
