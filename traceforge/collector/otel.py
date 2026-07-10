from datetime import datetime
from typing import Optional

from ..core import TraceCollector, TraceSpan


class OTELCollector(TraceCollector):
    def __init__(self, endpoint: Optional[str] = None, service_name: str = "traceforge"):
        self._spans: dict[str, TraceSpan] = {}
        self._traces: dict[str, list[str]] = {}
        self._trace_order: list[str] = []
        self._endpoint = endpoint
        self._service_name = service_name
        self._tracer_provider = None
        self._tracer = None

    def _ensure_tracer(self):
        if self._tracer is not None:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider()
            if self._endpoint:
                exporter = OTLPSpanExporter(endpoint=self._endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self._service_name)
        except ImportError:
            raise ImportError(
                "OpenTelemetry export requires: opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp"
            )

    def save(self, span: TraceSpan) -> None:
        self._spans[span.span_id] = span
        if span.trace_id not in self._traces:
            self._traces[span.trace_id] = []
            self._trace_order.append(span.trace_id)
        self._traces[span.trace_id].append(span.span_id)

        if span.parent_id and span.parent_id in self._spans:
            parent = self._spans[span.parent_id]
            if span.span_id not in parent.children:
                parent.children.append(span.span_id)

        self._export_to_otel(span)

    def _export_to_otel(self, span: TraceSpan) -> None:
        try:
            self._ensure_tracer()
            from opentelemetry import trace
            from opentelemetry.trace import SpanKind, Status, StatusCode

            otel_trace_id = int(span.trace_id.replace("-", "")[:32].ljust(32, "0"), 16)
            otel_span_id = int(span.span_id.replace("-", "")[:16].ljust(16, "0"), 16)
            otel_parent_id = None
            if span.parent_id:
                otel_parent_id = int(span.parent_id.replace("-", "")[:16].ljust(16, "0"), 16)

            context = trace.SpanContext(
                trace_id=otel_trace_id,
                span_id=otel_span_id,
                is_remote=False,
                trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
                trace_state=trace.TraceState(),
            )
            parent_context = None
            if otel_parent_id:
                parent_context = trace.SpanContext(
                    trace_id=otel_trace_id,
                    span_id=otel_parent_id,
                    is_remote=False,
                    trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
                    trace_state=trace.TraceState(),
                )

            with self._tracer.start_as_current_span(
                name=span.agent,
                context=trace.set_span_in_context(
                    trace.NonRecordingSpan(context),
                    parent_context and trace.set_span_in_context(trace.NonRecordingSpan(parent_context)),
                ),
                kind=SpanKind.INTERNAL,
            ) as otel_span:
                otel_span.set_attribute("agent", span.agent)
                if span.model:
                    otel_span.set_attribute("model", span.model)
                otel_span.set_attribute("tokens_input", span.tokens_input)
                otel_span.set_attribute("tokens_output", span.tokens_output)
                otel_span.set_attribute("cost_usd", span.cost_usd)
                otel_span.set_attribute("duration_ms", span.duration_ms)
                if span.error:
                    otel_span.set_attribute("error", span.error)
                if span.tags:
                    otel_span.set_attribute("tags", ",".join(span.tags))

                if span.status == "error":
                    otel_span.set_status(Status(StatusCode.ERROR, span.error or "unknown error"))
                else:
                    otel_span.set_status(Status(StatusCode.OK))
        except ImportError:
            pass

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

        for s in span_pool:
            if agent and s.agent != agent:
                continue
            if status and s.status != status:
                continue
            if min_duration_ms is not None and s.duration_ms < min_duration_ms:
                continue
            if since and s.started_at and s.started_at < since:
                continue
            results.append(s)

        return results

    def clear(self) -> None:
        self._spans.clear()
        self._traces.clear()
        self._trace_order.clear()

    def export_traces(self, since: Optional[datetime] = None) -> int:
        spans = self.query(since=since)
        count = 0
        for s in spans:
            self._export_to_otel(s)
            count += 1
        return count
