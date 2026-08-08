import sys
from datetime import datetime, timedelta

import pytest

from traceforge.core import TraceSpan


class _FakeQueryResult:
    def __init__(self, rows, columns):
        self.result_rows = rows
        self.column_names = columns


class _FakeClient:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.commands: list[str] = []
        self.closed = False
        self.connect_kwargs = None

    def command(self, sql: str):
        self.commands.append(sql)
        if sql.startswith("TRUNCATE"):
            self.store.clear()

    def insert(self, table, data, column_names=None):
        for row in data:
            rec = dict(zip(column_names, row))
            self.store[rec["span_id"]] = rec

    def query(self, sql, parameters=None):
        sql = " ".join(sql.split())
        params = parameters or {}
        if sql.startswith("SELECT * FROM spans FINAL WHERE trace_id ="):
            rows = [r for r in self.store.values() if r["trace_id"] == params["tid"]]
            rows.sort(key=lambda r: r["started_at"])
            return self._result(rows)
        if sql.startswith("SELECT * FROM spans FINAL WHERE span_id ="):
            rows = [r for r in self.store.values() if r["span_id"] == params["sid"]]
            return self._result(rows)
        if sql.startswith("SELECT span_id FROM spans FINAL WHERE parent_id ="):
            rows = [r for r in self.store.values() if r["parent_id"] == params["pid"]]
            return self._result([{"span_id": r["span_id"]} for r in rows])
        if sql.startswith("SELECT trace_id AS tid, MAX(started_at) AS t"):
            by_trace: dict[str, datetime] = {}
            for r in self.store.values():
                t = by_trace.get(r["trace_id"])
                if t is None or r["started_at"] > t:
                    by_trace[r["trace_id"]] = r["started_at"]
            ordered = sorted(by_trace.items(), key=lambda kv: kv[1], reverse=True)
            rows = [{"tid": tid, "t": t} for tid, t in ordered[: params["limit"]]]
            return self._result(rows)
        if sql.startswith("SELECT trace_id AS tid FROM spans ORDER BY"):
            rows = sorted(self.store.values(), key=lambda r: r["started_at"], reverse=True)
            return self._result([{"tid": r["trace_id"]} for r in rows[:1]])
        if sql.startswith("SELECT * FROM spans FINAL WHERE"):
            return self._result(self._filter(sql, params))
        raise AssertionError(f"Unhandled SQL: {sql}")

    def _filter(self, sql, params):
        inner = sql[len("SELECT * FROM spans FINAL WHERE ") :]
        where = inner.split(" ORDER BY")[0].strip()
        if where == "1 = 1":
            return list(self.store.values())
        result = []
        for row in self.store.values():
            ok = True
            for cond in where.split(" AND "):
                cond = cond.strip()
                if ">=" in cond:
                    col = cond.split(">=")[0].strip()
                    pname = cond.split("{")[1].split(":")[0]
                    ok = row.get(col) >= params[pname]
                else:
                    col = cond.split("=")[0].strip()
                    pname = cond.split("{")[1].split(":")[0]
                    ok = row.get(col) == params[pname]
                if not ok:
                    break
            if ok:
                result.append(row)
        return result

    def _result(self, rows):
        if not rows:
            return _FakeQueryResult([], [])
        columns = list(rows[0].keys())
        return _FakeQueryResult([tuple(r[c] for c in columns) for r in rows], columns)

    def close(self):
        self.closed = True


class _FakeClickHouseConnect:
    def __init__(self):
        self.client = _FakeClient()

    def get_client(self, **kwargs):
        self.client.connect_kwargs = kwargs
        return self.client


@pytest.fixture
def collector():
    fake = _FakeClickHouseConnect()
    sys.modules["clickhouse_connect"] = fake
    from traceforge.collector.clickhouse import ClickHouseCollector

    c = ClickHouseCollector(dsn="http://localhost:8123/default")
    yield c, fake
    c.close()


def test_save_and_retrieve(collector):
    c, _ = collector
    span = TraceSpan(agent="test", model="m1")
    c.save(span)

    retrieved = c.get_span(span.span_id)
    assert retrieved is not None
    assert retrieved.agent == "test"
    assert retrieved.model == "m1"


def test_streaming_fields_roundtrip(collector):
    c, _ = collector
    span = TraceSpan(
        agent="stream",
        model="gpt-4o",
        stream=True,
        ttft_ms=80.0,
        stream_chunks=3,
        chunk_offsets_ms=[5.0, 40.0, 80.0],
        output="hola",
        tokens_output=2,
    )
    c.save(span)

    retrieved = c.get_span(span.span_id)
    assert retrieved.stream is True
    assert retrieved.ttft_ms == pytest.approx(80.0)
    assert retrieved.stream_chunks == 3
    assert retrieved.chunk_offsets_ms == [5.0, 40.0, 80.0]
    assert retrieved.output == "hola"


def test_resave_does_not_duplicate(collector):
    c, _ = collector
    span = TraceSpan(agent="a", duration_ms=10, status="ok")
    c.save(span)
    span.duration_ms = 500
    span.status = "error"
    c.save(span)

    spans = c.get_trace(span.trace_id)
    assert len(spans) == 1
    assert spans[0].duration_ms == 500
    assert spans[0].status == "error"


def test_get_trace_builds_children_in_memory(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    c.save(root)
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    c.save(child)

    spans = c.get_trace(root.trace_id)
    by_agent = {s.agent: s for s in spans}
    assert by_agent["root"].children == [child.span_id]
    assert by_agent["child"].parent_id == root.span_id


def test_get_trace_links_child_saved_before_parent(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    c.save(child)
    c.save(root)

    spans = c.get_trace(root.trace_id)
    by_agent = {s.agent: s for s in spans}
    assert by_agent["root"].children == [child.span_id]


def test_get_span_computes_children(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    c.save(root)
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    c.save(child)

    retrieved = c.get_span(root.span_id)
    assert retrieved.children == [child.span_id]


def test_query_filters(collector):
    c, _ = collector
    ok = TraceSpan(agent="planner", status="ok", duration_ms=50)
    c.save(ok)
    slow = TraceSpan(agent="planner", status="error", duration_ms=500)
    c.save(slow)

    assert len(c.query(agent="planner")) == 2
    assert len(c.query(status="error")) == 1
    assert len(c.query(min_duration_ms=100)) == 1
    assert c.query(status="error")[0].span_id == slow.span_id


def test_query_by_since(collector):
    c, _ = collector
    old = TraceSpan(agent="a", started_at=datetime.now() - timedelta(days=10))
    c.save(old)
    recent = TraceSpan(agent="b", started_at=datetime.now())
    c.save(recent)

    since = datetime.now() - timedelta(days=1)
    assert [s.agent for s in c.query(since=since)] == ["b"]


def test_list_traces_and_last(collector):
    c, _ = collector
    base = datetime.now()
    ids = []
    for i in range(5):
        s = TraceSpan(agent="a", started_at=base + timedelta(seconds=i))
        c.save(s)
        ids.append(s.trace_id)

    assert c.get_last_trace_id() == ids[-1]
    recent = c.list_traces(limit=3)
    assert recent == list(reversed(ids[-3:]))


def test_clear(collector):
    c, _ = collector
    s = TraceSpan(agent="a")
    c.save(s)
    c.clear()
    assert c.get_span(s.span_id) is None
    assert c.get_last_trace_id() is None
    assert any("TRUNCATE" in cmd for cmd in c._client.commands)


def test_client_uses_parsed_dsn(collector):
    c, fake = collector
    kwargs = fake.client.connect_kwargs
    assert kwargs["host"] == "localhost"
    assert kwargs["port"] == 8123
    assert kwargs["database"] == "default"
    assert kwargs["secure"] is False


def test_missing_driver_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
    with pytest.raises(ImportError, match="pip install traceforge\\[clickhouse\\]"):
        from traceforge.collector.clickhouse import ClickHouseCollector

        ClickHouseCollector(dsn="http://localhost:8123/default")
