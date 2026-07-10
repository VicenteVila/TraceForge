from datetime import datetime, timedelta

import pytest

from traceforge.collector.memory import MemoryCollector
from traceforge.core import TraceSpan


@pytest.fixture
def collector():
    return MemoryCollector()


def test_save_and_retrieve_span(collector):
    span = TraceSpan(agent="test", model="m1")
    collector.save(span)

    retrieved = collector.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.agent == "test"
    assert retrieved.model == "m1"


def test_get_trace_returns_all_spans(collector):
    root = TraceSpan(agent="root")
    collector.save(root)

    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(child)

    spans = collector.get_trace(root.trace_id)
    assert len(spans) == 2


def test_list_traces_returns_recent(collector):
    ids = []
    for i in range(5):
        s = TraceSpan(agent="a")
        collector.save(s)
        ids.append(s.trace_id)

    recent = collector.list_traces(limit=3)
    assert len(recent) == 3
    assert recent == ids[-3:]


def test_get_last_trace_id(collector):
    assert collector.get_last_trace_id() is None

    s1 = TraceSpan(agent="a")
    collector.save(s1)
    assert collector.get_last_trace_id() == s1.trace_id

    s2 = TraceSpan(agent="b")
    collector.save(s2)
    assert collector.get_last_trace_id() == s2.trace_id


def test_query_by_agent(collector):
    planner = TraceSpan(agent="planner")
    collector.save(planner)
    developer = TraceSpan(agent="developer")
    collector.save(developer)

    results = collector.query(agent="planner")
    assert len(results) == 1
    assert results[0].agent == "planner"


def test_query_by_status(collector):
    ok = TraceSpan(agent="a", status="ok")
    collector.save(ok)
    err = TraceSpan(agent="b", status="error")
    collector.save(err)

    results = collector.query(status="error")
    assert len(results) == 1
    assert results[0].status == "error"


def test_query_by_min_duration(collector):
    fast = TraceSpan(agent="a")
    fast.duration_ms = 50
    collector.save(fast)
    slow = TraceSpan(agent="b")
    slow.duration_ms = 500
    collector.save(slow)

    results = collector.query(min_duration_ms=100)
    assert len(results) == 1
    assert results[0].duration_ms == 500


def test_query_by_since(collector):
    old = TraceSpan(agent="a", started_at=datetime.now() - timedelta(days=10))
    collector.save(old)
    recent = TraceSpan(agent="b", started_at=datetime.now())
    collector.save(recent)

    results = collector.query(since=datetime.now() - timedelta(days=1))
    assert len(results) == 1
    assert results[0].agent == "b"


def test_clear(collector):
    s = TraceSpan(agent="a")
    collector.save(s)
    collector.clear()
    assert collector.get_last_trace_id() is None
    assert collector.get_trace(s.trace_id) == []
