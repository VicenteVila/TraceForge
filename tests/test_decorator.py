import pytest

from traceforge import trace
from traceforge.collector.memory import MemoryCollector


@pytest.fixture
def mem_collector():
    return MemoryCollector()


def test_basic_sync_decorator(mem_collector):
    @trace(agent="test_agent", model="test-model", collector=mem_collector)
    def my_func(x: int) -> int:
        return x * 2

    result = my_func(21)
    assert result == 42

    last_trace = mem_collector.get_last_trace_id()
    assert last_trace is not None
    spans = mem_collector.get_trace(last_trace)
    assert len(spans) == 1
    assert spans[0].agent == "test_agent"
    assert spans[0].model == "test-model"
    assert spans[0].status == "ok"
    assert spans[0].duration_ms >= 0


def test_nested_traces(mem_collector):
    @trace(agent="outer", collector=mem_collector)
    def outer():
        return inner()

    @trace(agent="inner", collector=mem_collector)
    def inner():
        return 42

    result = outer()
    assert result == 42

    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 2

    outer_span = next(s for s in spans if s.agent == "outer")
    inner_span = next(s for s in spans if s.agent == "inner")

    assert inner_span.parent_id == outer_span.span_id
    assert inner_span.trace_id == outer_span.trace_id
    assert inner_span.span_id in outer_span.children


def test_exception_captures_error(mem_collector):
    @trace(agent="failing", collector=mem_collector)
    def will_fail():
        raise ValueError("something went wrong")

    with pytest.raises(ValueError, match="something went wrong"):
        will_fail()

    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 1
    assert spans[0].status == "error"
    assert "ValueError" in spans[0].error


def test_tags_are_recorded(mem_collector):
    @trace(agent="tagger", tags=["tag1", "tag2"], collector=mem_collector)
    def tagged_func():
        return "done"

    tagged_func()
    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert spans[0].tags == ["tag1", "tag2"]


def test_trace_id_persistence_across_calls(mem_collector):
    @trace(agent="first", collector=mem_collector)
    def first():
        return second()

    @trace(agent="second", collector=mem_collector)
    def second():
        return third()

    @trace(agent="third", collector=mem_collector)
    def third():
        return "done"

    first()
    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 3
    trace_ids = {s.trace_id for s in spans}
    assert len(trace_ids) == 1


def test_independent_calls_have_different_trace_ids(mem_collector):
    @trace(agent="worker", collector=mem_collector)
    def work():
        return "done"

    work()
    trace_a = mem_collector.get_last_trace_id()

    work()
    trace_b = mem_collector.get_last_trace_id()

    assert trace_a != trace_b
