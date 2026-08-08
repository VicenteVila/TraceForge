"""ClickHouse-backed collector for high-volume OLAP workloads.

Requires ``clickhouse-connect``: ``pip install traceforge[clickhouse]`` (HTTP
interface, port 8123). ClickHouse is append-only, so:
- spans are stored in a ``ReplacingMergeTree`` keyed by ``span_id`` (reads use
  ``FINAL`` so a re-saved span never duplicates),
- parent-child linking is computed at read time instead of re-writing parents.
"""

import json
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit

from ..core import TraceCollector, TraceSpan

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id String,
    trace_id String,
    parent_id Nullable(String),
    agent String,
    model Nullable(String),
    input Nullable(String),
    output Nullable(String),
    error Nullable(String),
    tokens_input Int32,
    tokens_output Int32,
    cost_usd Float64,
    started_at DateTime64(3),
    finished_at Nullable(DateTime64(3)),
    duration_ms Int64,
    status String,
    tags String,
    children String,
    input_truncated UInt8,
    output_truncated UInt8,
    stream UInt8,
    ttft_ms Nullable(Float64),
    stream_chunks UInt32,
    chunk_offsets_ms String
) ENGINE = ReplacingMergeTree()
ORDER BY span_id
"""

_DEFAULT_DSN = "http://localhost:8123/default"

_COLUMNS = [
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
]


class ClickHouseCollector(TraceCollector):
    def __init__(
        self,
        dsn: str = _DEFAULT_DSN,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._dsn = dsn
        self._database = database
        self._client = self._get_client(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
        self._client.command(_SCHEMA)

    def _get_client(self, host, port, database, username, password):
        try:
            import clickhouse_connect
        except ImportError:
            raise ImportError("ClickHouse support requires clickhouse-connect: pip install traceforge[clickhouse]")

        parsed = self._parse_dsn(self._dsn)
        db = database or parsed["database"] or "default"
        kwargs = dict(
            host=host or parsed["host"],
            port=port or parsed["port"],
            username=username or parsed["username"],
            password=password or parsed["password"],
            secure=parsed["secure"],
        )
        if db != "default":
            bootstrap = clickhouse_connect.get_client(database="default", **kwargs)
            bootstrap.command(f"CREATE DATABASE IF NOT EXISTS `{db}`")
            bootstrap.close()
        return clickhouse_connect.get_client(database=db, **kwargs)

    @staticmethod
    def _parse_dsn(dsn: str) -> dict[str, Any]:
        if "://" not in dsn:
            dsn = "http://" + dsn
        parsed = urlsplit(dsn)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 8123,
            "database": parsed.path.lstrip("/") or "default",
            "username": parsed.username,
            "password": parsed.password,
            "secure": parsed.scheme == "https",
        }

    def _serialize_span(self, span: TraceSpan) -> list[Any]:
        values = {
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
            "input_truncated": int(span.input_truncated),
            "output_truncated": int(span.output_truncated),
            "stream": int(span.stream),
            "ttft_ms": span.ttft_ms,
            "stream_chunks": span.stream_chunks,
            "chunk_offsets_ms": json.dumps(span.chunk_offsets_ms),
        }
        return [values[c] for c in _COLUMNS]

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
        )

    def _rows(self, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict]:
        result = self._client.query(sql, parameters=params or {})
        names = result.column_names
        return [dict(zip(names, r)) for r in result.result_rows]

    def save(self, span: TraceSpan) -> None:
        self._client.insert("spans", [self._serialize_span(span)], column_names=_COLUMNS)

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        rows = self._rows(
            "SELECT * FROM spans FINAL WHERE trace_id = {tid:String} ORDER BY started_at",
            {"tid": trace_id},
        )
        spans = [self._deserialize_span(r) for r in rows]
        children_by_parent: dict[Optional[str], list[str]] = {}
        for s in spans:
            children_by_parent.setdefault(s.parent_id, []).append(s.span_id)
        for s in spans:
            s.children = children_by_parent.get(s.span_id, [])
        return spans

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        rows = self._rows("SELECT * FROM spans FINAL WHERE span_id = {sid:String}", {"sid": span_id})
        if not rows:
            return None
        span = self._deserialize_span(rows[0])
        kids = self._rows(
            "SELECT span_id FROM spans FINAL WHERE parent_id = {pid:String}",
            {"pid": span_id},
        )
        span.children = [r["span_id"] for r in kids]
        return span

    def list_traces(self, limit: int = 10) -> list[str]:
        rows = self._rows(
            """
            SELECT trace_id AS tid, MAX(started_at) AS t
            FROM spans GROUP BY trace_id ORDER BY t DESC LIMIT {limit:UInt32}
            """,
            {"limit": limit},
        )
        return [r["tid"] for r in rows]

    def get_last_trace_id(self) -> Optional[str]:
        rows = self._rows("SELECT trace_id AS tid FROM spans ORDER BY started_at DESC LIMIT 1")
        return rows[0]["tid"] if rows else None

    def query(
        self,
        trace_id: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        since: Optional[datetime] = None,
    ) -> list[TraceSpan]:
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if trace_id:
            conditions.append("trace_id = {trace_id:String}")
            params["trace_id"] = trace_id
        if agent:
            conditions.append("agent = {agent:String}")
            params["agent"] = agent
        if status:
            conditions.append("status = {status:String}")
            params["status"] = status
        if min_duration_ms is not None:
            conditions.append("duration_ms >= {min_duration_ms:Int64}")
            params["min_duration_ms"] = min_duration_ms
        if since is not None:
            conditions.append("started_at >= {since:DateTime64}")
            params["since"] = since

        where = " AND ".join(conditions) if conditions else "1 = 1"
        rows = self._rows(f"SELECT * FROM spans FINAL WHERE {where} ORDER BY started_at", params)
        spans = [self._deserialize_span(r) for r in rows]
        children_by_parent: dict[Optional[str], list[str]] = {}
        for s in spans:
            children_by_parent.setdefault(s.parent_id, []).append(s.span_id)
        for s in spans:
            s.children = children_by_parent.get(s.span_id, [])
        return spans

    def clear(self) -> None:
        self._client.command("TRUNCATE TABLE spans")

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
