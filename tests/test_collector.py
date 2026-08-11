import threading
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


def test_list_traces_offset_pagination(collector):
    ids = []
    for i in range(5):
        s = TraceSpan(agent="a")
        collector.save(s)
        ids.append(s.trace_id)

    page1 = collector.list_traces(limit=2, offset=0)
    page2 = collector.list_traces(limit=2, offset=2)
    page3 = collector.list_traces(limit=2, offset=4)
    assert page1 == ids[-2:]
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
    assert len(set(page1 + page2 + page3)) == 5
    assert collector.list_traces(limit=2, offset=99) == []


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


def test_parent_saved_after_child_links_children(collector):
    root = TraceSpan(agent="root")
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(child)

    collector.save(root)

    assert root.children == [child.span_id]


def test_sibling_parents_linked_independently(collector):
    root = TraceSpan(agent="root")
    collector.save(root)

    first = TraceSpan(agent="a", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(first)

    second = TraceSpan(agent="b", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(second)

    assert set(root.children) == {first.span_id, second.span_id}


def test_concurrent_saves_from_multiple_threads():
    collector = MemoryCollector()
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                s = TraceSpan(agent=f"w{n}", model=f"m{i}")
                collector.save(s)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(collector.list_traces(limit=1000)) == 8 * 50


def test_concurrent_parent_child_linking():
    collector = MemoryCollector()
    root = TraceSpan(agent="root")
    collector.save(root)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            child = TraceSpan(agent=f"child-{n}", parent_id=root.span_id, trace_id=root.trace_id)
            collector.save(child)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(root.children) == 10
    for cid in root.children:
        assert collector.get_span(cid).parent_id == root.span_id
