"""PostgreSQL-backed collector for production workloads.

Requires ``psycopg`` (v3): ``pip install traceforge[postgres]``. The driver is
imported lazily so the package works without it. Uses one connection per thread
(mirroring the SQLite collector) and a read-modify-write transaction in
:meth:`save` so parent-child linking is atomic under concurrent writers.
"""

import json
import threading
from typing import Any, Optional

from ..core import TraceCollector, TraceSpan, _metadata_contains

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_id TEXT,
    agent TEXT NOT NULL,
    model TEXT,
    input TEXT,
    output TEXT,
    error TEXT,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    tags TEXT NOT NULL DEFAULT '[]',
    children TEXT NOT NULL DEFAULT '[]',
    input_truncated BOOLEAN NOT NULL DEFAULT FALSE,
    output_truncated BOOLEAN NOT NULL DEFAULT FALSE,
    stream BOOLEAN NOT NULL DEFAULT FALSE,
    ttft_ms DOUBLE PRECISION,
    stream_chunks INTEGER NOT NULL DEFAULT 0,
    chunk_offsets_ms TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_agent ON spans(agent);
CREATE INDEX IF NOT EXISTS idx_spans_status ON spans(status);
CREATE INDEX IF NOT EXISTS idx_spans_started_at ON spans(started_at);
"""

_DEFAULT_DSN = "postgresql://localhost/traceforge"

_COLUMNS = (
    "span_id",
    "trace_id",
    "parent_id",
    "agent",
    "model",
    "input",
    "output",
    "error",
    "tokens_input",
    "tokens_output",
    "cost_usd",
    "started_at",
    "finished_at",
    "duration_ms",
    "status",
    "tags",
    "children",
    "input_truncated",
    "output_truncated",
    "stream",
    "ttft_ms",
    "stream_chunks",
    "chunk_offsets_ms",
    "metadata",
)


class PostgresCollector(TraceCollector):
    def __init__(self, dsn: str = _DEFAULT_DSN):
        self._dsn = dsn
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._connections: set[Any] = set()
        self._psycopg = None
        self._create_schema()

    def _get_driver(self):
        if self._psycopg is None:
            try:
                import psycopg
            except ImportError:
                raise ImportError("PostgreSQL support requires psycopg: pip install traceforge[postgres]")
            self._psycopg = psycopg
        return self._psycopg

    def _get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            psycopg = self._get_driver()
            conn = psycopg.connect(self._dsn)
            conn.row_factory = psycopg.rows.dict_row
            self._local.conn = conn
            with self._conn_lock:
                self._connections.add(conn)
        return conn

    def _create_schema(self) -> None:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
            try:
                cur.execute("ALTER TABLE spans ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '{}'")
            except Exception:
                conn.rollback()
                with conn.cursor() as c2:
                    c2.execute(_SCHEMA)
        conn.commit()

    def _serialize_span(self, span: TraceSpan) -> dict[str, Any]:
        return {
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_id": span.parent_id,
            "agent": span.agent,
            "model": span.model,
            "input": json.dumps(span.input, default=str, ensure_ascii=False) if span.input is not None else None,
            "output": json.dumps(span.output, default=str, ensure_ascii=False) if span.output is not None else None,
            "error": span.error,
            "tokens_input": span.tokens_input,
            "tokens_output": span.tokens_output,
            "cost_usd": span.cost_usd,
            "started_at": span.started_at,
            "finished_at": span.finished_at,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "tags": json.dumps(span.tags, ensure_ascii=False),
            "children": json.dumps(span.children, ensure_ascii=False),
            "input_truncated": span.input_truncated,
            "output_truncated": span.output_truncated,
            "stream": span.stream,
            "ttft_ms": span.ttft_ms,
            "stream_chunks": span.stream_chunks,
            "chunk_offsets_ms": json.dumps(span.chunk_offsets_ms),
            "metadata": json.dumps(span.metadata or {}, ensure_ascii=False, default=str),
        }

    def _deserialize_span(self, row: dict) -> TraceSpan:
        return TraceSpan(
            span_id=row["span_id"],
            trace_id=row["trace_id"],
            parent_id=row["parent_id"],
            agent=row["agent"],
            model=row["model"],
            input=json.loads(row["input"]) if row["input"] else None,
            output=json.loads(row["output"]) if row["output"] else None,
            error=row["error"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_usd=row["cost_usd"],
            input_truncated=bool(row["input_truncated"]),
            output_truncated=bool(row["output_truncated"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            duration_ms=row["duration_ms"],
            status=row["status"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            children=json.loads(row["children"]) if row["children"] else [],
            stream=bool(row["stream"]),
            ttft_ms=row["ttft_ms"],
            stream_chunks=row["stream_chunks"],
            chunk_offsets_ms=json.loads(row["chunk_offsets_ms"]) if row["chunk_offsets_ms"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def save(self, span: TraceSpan) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT children FROM spans WHERE span_id = %s", (span.span_id,))
                row = cur.fetchone()
                if row:
                    existing: list[str] = json.loads(row["children"]) if row["children"] else []
                    for cid in existing:
                        if cid not in span.children:
                            span.children.append(cid)

                cur.execute("SELECT span_id FROM spans WHERE parent_id = %s", (span.span_id,))
                for row in cur.fetchall():
                    cid = row["span_id"]
                    if cid not in span.children:
                        span.children.append(cid)

                data = self._serialize_span(span)
                data["children"] = json.dumps(span.children, ensure_ascii=False)
                placeholders = ", ".join(f"%({c})s" for c in _COLUMNS)
                updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS)
                cur.execute(
                    f"""
                    INSERT INTO spans ({", ".join(_COLUMNS)}) VALUES ({placeholders})
                    ON CONFLICT (span_id) DO UPDATE SET {updates}
                    """,
                    data,
                )

                if span.parent_id:
                    cur.execute("SELECT children FROM spans WHERE span_id = %s", (span.parent_id,))
                    prow = cur.fetchone()
                    if prow:
                        children: list[str] = json.loads(prow["children"]) if prow["children"] else []
                        if span.span_id not in children:
                            children.append(span.span_id)
                            cur.execute(
                                "UPDATE spans SET children = %s WHERE span_id = %s",
                                (json.dumps(children, ensure_ascii=False), span.parent_id),
                            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM spans WHERE trace_id = %s ORDER BY started_at", (trace_id,))
            return [self._deserialize_span(row) for row in cur.fetchall()]

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM spans WHERE span_id = %s", (span_id,))
            row = cur.fetchone()
            return self._deserialize_span(row) if row else None

    def list_traces(self, limit: int = 10, offset: int = 0) -> list[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trace_id FROM spans
                GROUP BY trace_id
                ORDER BY MAX(started_at) DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return [row["trace_id"] for row in cur.fetchall()]

    def get_last_trace_id(self) -> Optional[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT trace_id FROM spans ORDER BY started_at DESC LIMIT 1")
            row = cur.fetchone()
            return row["trace_id"] if row else None

    def query(
        self,
        trace_id: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        since: Any = None,
        metadata: Optional[dict] = None,
    ) -> list[TraceSpan]:
        conditions: list[str] = []
        params: list[Any] = []
        if trace_id:
            conditions.append("trace_id = %s")
            params.append(trace_id)
        if agent:
            conditions.append("agent = %s")
            params.append(agent)
        if status:
            conditions.append("status = %s")
            params.append(status)
        if min_duration_ms is not None:
            conditions.append("duration_ms >= %s")
            params.append(min_duration_ms)
        if since is not None:
            conditions.append("started_at >= %s")
            params.append(since)

        where = " AND ".join(conditions) if conditions else "TRUE"
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM spans WHERE {where} ORDER BY started_at", params)
            spans = [self._deserialize_span(row) for row in cur.fetchall()]
        if metadata:
            spans = [s for s in spans if _metadata_contains(s.metadata or {}, metadata)]
        return spans

    def clear(self) -> None:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM spans")
        conn.commit()

    def close(self) -> None:
        with self._conn_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
