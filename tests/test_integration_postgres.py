"""Integration tests against a real Postgres server.

Run with a local instance (see docker-compose.yml):
    TRACEFORGE_TEST_PG_DSN="postgresql://traceforge:traceforge@localhost:5432/traceforge" \
        pytest tests/test_integration_postgres.py
"""

import os
from datetime import datetime, timedelta

import pytest

from traceforge.collector.postgres import PostgresCollector
from traceforge.core import TraceSpan

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRACEFORGE_TEST_PG_DSN"),
    reason="set TRACEFORGE_TEST_PG_DSN to run Postgres integration tests",
)


@pytest.fixture
def collector():
    c = PostgresCollector(os.environ["TRACEFORGE_TEST_PG_DSN"])
    c.clear()
    yield c
    c.clear()
    c.close()


def test_save_and_retrieve(collector):
    span = TraceSpan(agent="test", model="m1", output="hello")
    collector.save(span)
    retrieved = collector.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.agent == "test"
    assert retrieved.model == "m1"
    assert retrieved.output == "hello"


def test_parent_child_linking(collector):
    root = TraceSpan(agent="root")
    collector.save(root)
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    collector.save(child)

    spans = collector.get_trace(root.trace_id)
    assert len(spans) == 2
    assert collector.get_span(root.span_id).children == [child.span_id]


def test_query_filters(collector):
    ok = TraceSpan(agent="planner", status="ok", duration_ms=50)
    collector.save(ok)
    slow = TraceSpan(agent="planner", status="error", duration_ms=500)
    collector.save(slow)

    assert len(collector.query(agent="planner")) == 2
    assert len(collector.query(status="error")) == 1
    assert len(collector.query(min_duration_ms=100)) == 1


def test_query_by_since(collector):
    old = TraceSpan(agent="a", started_at=datetime.now() - timedelta(days=10))
    collector.save(old)
    recent = TraceSpan(agent="b", started_at=datetime.now())
    collector.save(recent)

    since = datetime.now() - timedelta(days=1)
    assert [s.agent for s in collector.query(since=since)] == ["b"]


def test_list_traces(collector):
    ids = []
    for i in range(3):
        s = TraceSpan(agent="a", started_at=datetime.now() + timedelta(seconds=i))
        collector.save(s)
        ids.append(s.trace_id)

    assert collector.get_last_trace_id() == ids[-1]
    assert len(collector.list_traces(limit=10)) >= 3
