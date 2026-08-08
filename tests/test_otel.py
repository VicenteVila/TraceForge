import sys
from unittest import mock

from traceforge.collector.otel import (
    OTELCollector,
    _DeterministicIdGenerator,
    _otel_span_id,
    _otel_trace_id,
    _to_ns,
)
from traceforge.core import TraceSpan


class _FakeTraceFlags:
    SAMPLED = 0x01

    def __init__(self, value):
        self.value = value


class _FakeTraceState:
    def __init__(self, *args, **kwargs):
        pass


class _FakeSpanContext:
    def __init__(self, trace_id, span_id, is_remote, trace_flags, trace_state):
        self.trace_id = trace_id
        self.span_id = span_id


class _FakeNonRecordingSpan:
    def __init__(self, context):
        self.context = context


def _fake_set_span_in_context(span, context=None):
    return {"span": span, "parent": context}


class _FakeSpan:
    def __init__(self, name, context, kind, start_time):
        self.name = name
        self.context = context
        self.kind = kind
        self.start_time = start_time
        self.attributes = {}
        self.status = None
        self.end_time = None

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, status):
        self.status = status

    def end(self, end_time=None):
        self.end_time = end_time


class _FakeTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, context=None, kind=None, start_time=None, **kwargs):
        span = _FakeSpan(name, context, kind, start_time)
        self.spans.append(span)
        return span


def _install_fake_otel(monkeypatch):
    trace_mod = mock.MagicMock()
    trace_mod.SpanContext = _FakeSpanContext
    trace_mod.TraceFlags = _FakeTraceFlags
    trace_mod.TraceState = _FakeTraceState
    trace_mod.NonRecordingSpan = _FakeNonRecordingSpan
    trace_mod.set_span_in_context = _fake_set_span_in_context
    trace_mod.SpanKind = mock.MagicMock(INTERNAL="INTERNAL")
    trace_mod.Status = mock.MagicMock(return_value="status")
    trace_mod.StatusCode = mock.MagicMock(ERROR="ERROR", OK="OK")

    otel_pkg = mock.MagicMock()
    otel_pkg.trace = trace_mod

    monkeypatch.setitem(sys.modules, "opentelemetry", otel_pkg)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_mod)
    return trace_mod


def test_otel_id_conversion_is_deterministic():
    trace_id = "12345678-1234-1234-1234-123456789012"
    span_id = "abcdef12-3456-7890-abcd-ef1234567890"
    assert _otel_trace_id(trace_id) == int(trace_id.replace("-", ""), 16)
    assert _otel_span_id(span_id) == _otel_span_id(span_id)
    assert 0 < _otel_span_id(span_id) < 2**64
    assert _otel_span_id("00000000-0000-0000-0000-000000000001") != _otel_span_id(
        "00000000-0000-0000-0000-000000000002"
    )


def test_deterministic_id_generator_consumes_pending():
    gen = _DeterministicIdGenerator()
    gen._pending_trace_id.value = 123
    gen._pending_span_id.value = 456
    assert gen.generate_trace_id() == 123
    assert gen.generate_span_id() == 456
    assert gen.generate_trace_id() != 123
    assert gen.generate_span_id() != 456


def _saved_span(agent="a", parent_id=None, trace_id="00000000-0000-0000-0000-000000000001"):
    span = TraceSpan(agent=agent, parent_id=parent_id, trace_id=trace_id)
    span.set_tokens(input=1000, output=500)
    span.close()
    return span


def test_export_uses_real_timestamps(monkeypatch):
    _install_fake_otel(monkeypatch)
    tracer = _FakeTracer()
    collector = OTELCollector()
    collector._tracer = tracer

    span = _saved_span()
    collector.save(span)

    exported = tracer.spans[0]
    assert exported.name == "a"
    assert exported.start_time == _to_ns(span.started_at)
    assert exported.end_time == _to_ns(span.finished_at)
    assert exported.attributes["tokens_input"] == 1000
    assert exported.attributes["tokens_output"] == 500
    assert "model" not in exported.attributes


def test_export_status_error(monkeypatch):
    _install_fake_otel(monkeypatch)
    tracer = _FakeTracer()
    collector = OTELCollector()
    collector._tracer = tracer

    span = TraceSpan(agent="a")
    span.set_error("boom")
    span.close()
    collector.save(span)

    exported = tracer.spans[0]
    assert exported.status == "status"
    assert exported.attributes["error"] == "boom"


def test_export_links_parent_context(monkeypatch):
    _install_fake_otel(monkeypatch)
    tracer = _FakeTracer()
    collector = OTELCollector()
    collector._tracer = tracer

    root = _saved_span(agent="root")
    collector.save(root)

    child = _saved_span(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(child)

    child_exported = tracer.spans[1]
    parent_span = child_exported.context["span"]
    assert parent_span.context.span_id == _otel_span_id(root.span_id)
