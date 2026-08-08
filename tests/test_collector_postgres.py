import sys
import threading
from datetime import datetime, timedelta

import pytest

from traceforge.core import TraceSpan


class _FakeDB:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.committed = 0

    def execute(self, sql: str, params=None):
        sql = " ".join(sql.split())
        params = params or ()

        if sql.startswith("CREATE TABLE"):
            return None
        if sql.startswith("SELECT children FROM spans WHERE span_id ="):
            row = self.store.get(params[0])
            return [row] if row else []
        if sql.startswith("SELECT span_id FROM spans WHERE parent_id ="):
            pid = params[0]
            return [{"span_id": sid} for sid, r in self.store.items() if r["parent_id"] == pid]
        if sql.startswith("INSERT INTO spans"):
            rec = dict(params)
            self.store[rec["span_id"]] = rec
            return []
        if sql.startswith("UPDATE spans SET children ="):
            children, span_id = params
            if span_id in self.store:
                self.store[span_id]["children"] = children
            return []
        if sql.startswith("SELECT * FROM spans WHERE"):
            return self._select_where(sql, params)
        if sql.startswith("SELECT trace_id FROM spans GROUP BY"):
            limit = params[0]
            by_trace: dict[str, datetime] = {}
            for r in self.store.values():
                t = by_trace.get(r["trace_id"])
                if t is None or r["started_at"] > t:
                    by_trace[r["trace_id"]] = r["started_at"]
            ordered = sorted(by_trace.items(), key=lambda kv: kv[1], reverse=True)
            return [{"trace_id": tid} for tid, _ in ordered[:limit]]
        if sql.startswith("SELECT trace_id FROM spans ORDER BY"):
            rows = sorted(self.store.values(), key=lambda r: r["started_at"], reverse=True)
            return [{"trace_id": r["trace_id"]} for r in rows[:1]]
        if sql.startswith("DELETE FROM spans"):
            self.store.clear()
            return []
        raise AssertionError(f"Unhandled SQL: {sql}")

    def _select_where(self, sql, params):
        inner = sql[len("SELECT * FROM spans WHERE ") :]
        where = inner.split(" ORDER BY")[0].strip()
        rows = list(self.store.values())
        if where == "TRUE":
            return rows
        result = []
        for row in rows:
            ok = True
            pi = 0
            for cond in where.split(" AND "):
                cond = cond.strip()
                if ">=" in cond:
                    col = cond.split(">=")[0].strip()
                    ok = row.get(col) >= params[pi]
                else:
                    col = cond.split("=")[0].strip()
                    ok = row.get(col) == params[pi]
                pi += 1
                if not ok:
                    break
            if ok:
                result.append(row)
        return result


class _FakeCursor:
    def __init__(self, db):
        self._db = db
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._rows = self._db.execute(sql, params) or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, db):
        self._db = db
        self.row_factory = None
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._db)

    def commit(self):
        self._db.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _FakePsycopg:
    class rows:
        dict_row = object()

    def __init__(self):
        self.db = _FakeDB()

    def connect(self, dsn):
        return _FakeConnection(self.db)


@pytest.fixture
def collector():
    fake = _FakePsycopg()
    sys.modules["psycopg"] = fake
    from traceforge.collector.postgres import PostgresCollector

    c = PostgresCollector(dsn="postgresql://test")
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
        ttft_ms=120.5,
        stream_chunks=4,
        chunk_offsets_ms=[10.0, 50.0, 90.0, 120.5],
        tokens_input=3,
        tokens_output=7,
        output="hello",
        input_truncated=True,
    )
    c.save(span)

    retrieved = c.get_span(span.span_id)
    assert retrieved.stream is True
    assert retrieved.ttft_ms == pytest.approx(120.5)
    assert retrieved.stream_chunks == 4
    assert retrieved.chunk_offsets_ms == [10.0, 50.0, 90.0, 120.5]
    assert retrieved.output == "hello"
    assert retrieved.input_truncated is True


def test_save_update_existing(collector):
    c, _ = collector
    span = TraceSpan(agent="test", duration_ms=100, status="ok")
    c.save(span)
    span.duration_ms = 200
    span.status = "error"
    c.save(span)

    retrieved = c.get_span(span.span_id)
    assert retrieved.duration_ms == 200
    assert retrieved.status == "error"


def test_get_trace_returns_all_spans(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    c.save(root)
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    c.save(child)

    assert len(c.get_trace(root.trace_id)) == 2


def test_parent_saved_after_child_links(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    child = TraceSpan(agent="child", parent_id=root.span_id, trace_id=root.trace_id)
    c.save(child)
    c.save(root)

    retrieved = c.get_span(root.span_id)
    assert retrieved.children == [child.span_id]


def test_sibling_linking(collector):
    c, _ = collector
    root = TraceSpan(agent="root")
    c.save(root)
    for i in range(3):
        s = TraceSpan(agent=f"a{i}", parent_id=root.span_id, trace_id=root.trace_id)
        c.save(s)

    retrieved = c.get_span(root.span_id)
    assert len(retrieved.children) == 3


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
    assert len(c.query(since=since)) == 1
    assert c.query(since=since)[0].agent == "b"


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


def test_concurrent_saves(collector):
    c, _ = collector
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(30):
                c.save(TraceSpan(agent=f"w{n}", model=f"m{i}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(c.list_traces(limit=1000)) == 4 * 30


def test_missing_driver_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError, match="pip install traceforge\\[postgres\\]"):
        from traceforge.collector.postgres import PostgresCollector

        PostgresCollector(dsn="postgresql://test")
