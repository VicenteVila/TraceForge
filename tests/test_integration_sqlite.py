import os

import pytest

from traceforge import configure, get_last_trace_id, list_traces, query, trace
from traceforge.pricing import calculate_cost


@pytest.fixture
def sqlite_collector(tmp_path):
    db_path = str(tmp_path / "integration.db")
    configure(collector="sqlite", db_path=db_path)
    yield db_path
    from traceforge import _collector

    if hasattr(_collector, "close"):
        _collector.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_sqlite_decorator_persists(sqlite_collector):
    @trace(agent="test_agent", model="gemini-2.5-flash")
    def my_func(x: int) -> int:
        return x * 2

    result = my_func(21)
    assert result == 42

    trace_id = get_last_trace_id()
    assert trace_id is not None

    spans = query(trace_id=trace_id)
    assert len(spans) == 1
    assert spans[0].agent == "test_agent"
    assert spans[0].status == "ok"


def test_sqlite_nested_traces(sqlite_collector):
    @trace(agent="outer", model=None)
    def outer():
        return inner()

    @trace(agent="inner", model="llama-3.3-70b")
    def inner():
        return 42

    result = outer()
    assert result == 42

    trace_id = get_last_trace_id()
    spans = query(trace_id=trace_id)
    assert len(spans) == 2

    outer_span = next(s for s in spans if s.agent == "outer")
    inner_span = next(s for s in spans if s.agent == "inner")
    assert inner_span.parent_id == outer_span.span_id
    assert inner_span.trace_id == outer_span.trace_id


def test_sqlite_cost_calculation(sqlite_collector):
    @trace(agent="costly", model="gpt-4o-mini")
    def costly_func() -> str:
        from traceforge import span

        with span(agent="costly_span", model="gpt-4o-mini") as sp:
            sp.set_tokens(input=500, output=300)
        return "done"

    costly_func()

    trace_id = get_last_trace_id()
    spans = query(trace_id=trace_id)

    next(s for s in spans if s.agent == "costly")
    inner = next(s for s in spans if s.agent == "costly_span")

    assert inner.cost_usd > 0
    expected_inner = calculate_cost("gpt-4o-mini", 500, 300)
    assert inner.cost_usd == pytest.approx(expected_inner)


def test_sqlite_list_and_query(sqlite_collector):
    @trace(agent="worker", model="gemini-2.0-flash")
    def work():
        return "done"

    for i in range(3):
        work()

    trace_ids = list_traces(limit=5)
    assert len(trace_ids) >= 3

    query(status="error")
    ok_spans = query(status="ok")
    assert len(ok_spans) > 0
