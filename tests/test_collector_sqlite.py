import os
from datetime import datetime, timedelta

import pytest

from traceforge.collector.sqlite import SQLiteCollector
from traceforge.core import TraceSpan


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_traces.db")


@pytest.fixture
def collector(db_path):
    c = SQLiteCollector(db_path)
    yield c
    c.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_save_and_retrieve(collector):
    span = TraceSpan(agent="test", model="m1")
    collector.save(span)

    retrieved = collector.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.agent == "test"
    assert retrieved.model == "m1"
    assert retrieved.span_id == span.span_id


def test_save_update_existing(collector):
    span = TraceSpan(agent="test", duration_ms=100, status="ok")
    collector.save(span)

    span.duration_ms = 200
    span.status = "error"
    collector.save(span)

    retrieved = collector.get_span(span.span_id)
    assert retrieved.duration_ms == 200
    assert retrieved.status == "error"


def test_get_trace_returns_all_spans(collector):
    root = TraceSpan(agent="root")
    collector.save(root)

    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(child)

    spans = collector.get_trace(root.trace_id)
    assert len(spans) == 2


def test_get_trace_empty(collector):
    spans = collector.get_trace("nonexistent")
    assert spans == []


def test_list_traces_returns_recent(collector):
    ids = []
    for i in range(5):
        s = TraceSpan(agent="a")
        collector.save(s)
        ids.append(s.trace_id)

    recent = collector.list_traces(limit=3)
    assert len(recent) == 3
    for tid in recent:
        assert tid in ids


def test_get_last_trace_id(collector):
    assert collector.get_last_trace_id() is None
    s1 = TraceSpan(agent="a")
    collector.save(s1)
    assert collector.get_last_trace_id() == s1.trace_id


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


def test_tags_serialization(collector):
    span = TraceSpan(agent="a", tags=["tag1", "tag2", "tag3"])
    collector.save(span)
    retrieved = collector.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.tags == ["tag1", "tag2", "tag3"]


def test_children_serialization(collector):
    span = TraceSpan(agent="parent", children=["child1", "child2"])
    collector.save(span)
    retrieved = collector.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.children == ["child1", "child2"]
