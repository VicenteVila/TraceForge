import pytest

from traceforge import span, trace
from traceforge.collector.memory import MemoryCollector


def test_span_context_manager_basic():
    collector = MemoryCollector()
    with span(agent="ctx_agent", model="ctx_model", collector=collector) as sp:
        sp.set_output("ctx_result")
        sp.set_tokens(input=100, output=50)

    assert sp.status == "ok"
    assert sp.output == "ctx_result"
    assert sp.tokens_input == 100
    assert sp.tokens_output == 50
    assert sp.duration_ms >= 0

    stored = collector.get_span(sp.span_id)
    assert stored is not None


def test_span_nested_inside_decorator():
    collector = MemoryCollector()

    @trace(agent="outer", collector=collector)
    def outer_func():
        with span(agent="inner_span", collector=collector) as sp:
            sp.set_output("nested")
        return "done"

    outer_func()
    spans = collector.get_trace(collector.get_last_trace_id())
    assert len(spans) == 2

    outer = next(s for s in spans if s.agent == "outer")
    inner = next(s for s in spans if s.agent == "inner_span")
    assert inner.parent_id == outer.span_id
    assert inner.trace_id == outer.trace_id


def test_span_captures_exception():
    collector = MemoryCollector()
    with pytest.raises(RuntimeError):
        with span(agent="failing_span", collector=collector) as sp:
            raise RuntimeError("span error")

    assert sp.status == "error"
    assert "RuntimeError" in sp.error
    assert sp.duration_ms >= 0
