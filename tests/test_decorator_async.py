import asyncio

import pytest

from traceforge import span, trace
from traceforge.collector.memory import MemoryCollector


@pytest.fixture
def mem_collector():
    return MemoryCollector()


@pytest.mark.asyncio
async def test_basic_async_decorator(mem_collector):
    @trace(agent="async_agent", model="test-model", collector=mem_collector)
    async def my_async_func(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    result = await my_async_func(21)
    assert result == 42

    trace_id = mem_collector.get_last_trace_id()
    assert trace_id is not None
    spans = mem_collector.get_trace(trace_id)
    assert len(spans) == 1
    assert spans[0].agent == "async_agent"
    assert spans[0].status == "ok"
    assert spans[0].duration_ms >= 10


@pytest.mark.asyncio
async def test_nested_async_traces(mem_collector):
    @trace(agent="outer", collector=mem_collector)
    async def outer():
        return await inner()

    @trace(agent="inner", collector=mem_collector)
    async def inner():
        await asyncio.sleep(0.01)
        return 42

    result = await outer()
    assert result == 42

    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 2

    outer_span = next(s for s in spans if s.agent == "outer")
    inner_span = next(s for s in spans if s.agent == "inner")

    assert inner_span.parent_id == outer_span.span_id
    assert inner_span.trace_id == outer_span.trace_id
    assert inner_span.span_id in outer_span.children


@pytest.mark.asyncio
async def test_async_exception_captures_error(mem_collector):
    @trace(agent="failing_async", collector=mem_collector)
    async def will_fail():
        await asyncio.sleep(0.01)
        raise ValueError("async error")

    with pytest.raises(ValueError, match="async error"):
        await will_fail()

    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 1
    assert spans[0].status == "error"
    assert "ValueError" in spans[0].error


@pytest.mark.asyncio
async def test_async_tags_are_recorded(mem_collector):
    @trace(agent="tagger", tags=["async", "test"], collector=mem_collector)
    async def tagged():
        return "done"

    await tagged()
    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert spans[0].tags == ["async", "test"]


@pytest.mark.asyncio
async def test_async_trace_id_persistence(mem_collector):
    @trace(agent="first", collector=mem_collector)
    async def first():
        return await second()

    @trace(agent="second", collector=mem_collector)
    async def second():
        return await third()

    @trace(agent="third", collector=mem_collector)
    async def third():
        return "done"

    await first()
    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 3
    trace_ids = {s.trace_id for s in spans}
    assert len(trace_ids) == 1


@pytest.mark.asyncio
async def test_independent_async_calls_have_different_trace_ids(mem_collector):
    @trace(agent="worker", collector=mem_collector)
    async def work():
        await asyncio.sleep(0.01)
        return "done"

    await work()
    trace_a = mem_collector.get_last_trace_id()

    await work()
    trace_b = mem_collector.get_last_trace_id()

    assert trace_a != trace_b


@pytest.mark.asyncio
async def test_async_span_context_manager(mem_collector):
    with span(agent="async_span", model="test", collector=mem_collector) as sp:
        sp.set_output("ctx_result")
        sp.set_tokens(input=100, output=50)

    assert sp.status == "ok"
    assert sp.output == "ctx_result"
    assert sp.tokens_input == 100
    assert sp.tokens_output == 50

    stored = mem_collector.get_span(sp.span_id)
    assert stored is not None
    assert stored.agent == "async_span"


@pytest.mark.asyncio
async def test_sync_called_from_async(mem_collector):
    @trace(agent="sync_func", collector=mem_collector)
    def sync_work():
        return 42

    @trace(agent="async_caller", collector=mem_collector)
    async def async_caller():
        return sync_work()

    result = await async_caller()
    assert result == 42

    spans = mem_collector.get_trace(mem_collector.get_last_trace_id())
    assert len(spans) == 2

    async_span = next(s for s in spans if s.agent == "async_caller")
    sync_span = next(s for s in spans if s.agent == "sync_func")
    assert sync_span.parent_id == async_span.span_id
    assert sync_span.trace_id == async_span.trace_id


@pytest.mark.asyncio
async def test_concurrent_traces_are_independent(mem_collector):
    @trace(agent="worker", collector=mem_collector)
    async def work(label: str):
        await asyncio.sleep(0.02)
        return label

    results = await asyncio.gather(work("A"), work("B"), work("C"))
    assert sorted(results) == ["A", "B", "C"]

    trace_ids = mem_collector.list_traces(limit=5)
    assert len(trace_ids) == 3
    assert len(set(trace_ids)) == 3
