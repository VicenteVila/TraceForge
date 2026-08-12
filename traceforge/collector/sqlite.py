import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core import TraceCollector, TraceSpan, _metadata_json_path


class SQLiteCollector(TraceCollector):
    """SQLite-backed collector safe for concurrent use.

    Each thread owns its own connection (no shared ``check_same_thread=False``
    connection). Write operations use ``BEGIN IMMEDIATE`` so the read-modify-write
    in :meth:`save` is atomic even with concurrent writers, while WAL mode keeps
    readers non-blocking.
    """

    def __init__(self, db_path: str = "traces.db"):
        self._db_path = str(Path(db_path).resolve())
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._connections: set[sqlite3.Connection] = set()
        self._create_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            with self._conn_lock:
                self._connections.add(conn)
        return conn

    def _create_schema(self) -> None:
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_id TEXT,
                agent TEXT NOT NULL,
                model TEXT,
                input TEXT,
                output TEXT,
                error TEXT,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok',
                tags TEXT DEFAULT '[]',
                children TEXT DEFAULT '[]',
                input_truncated INTEGER DEFAULT 0,
                output_truncated INTEGER DEFAULT 0,
                stream INTEGER DEFAULT 0,
                ttft_ms REAL,
                stream_chunks INTEGER DEFAULT 0,
                chunk_offsets_ms TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
            CREATE INDEX IF NOT EXISTS idx_spans_agent ON spans(agent);
            CREATE INDEX IF NOT EXISTS idx_spans_status ON spans(status);
            CREATE INDEX IF NOT EXISTS idx_spans_started_at ON spans(started_at);
        """)
        existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(spans)")]
        if "input_truncated" not in existing_columns:
            conn.execute("ALTER TABLE spans ADD COLUMN input_truncated INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE spans ADD COLUMN output_truncated INTEGER DEFAULT 0")
        if "stream" not in existing_columns:
            conn.execute("ALTER TABLE spans ADD COLUMN stream INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE spans ADD COLUMN ttft_ms REAL")
            conn.execute("ALTER TABLE spans ADD COLUMN stream_chunks INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE spans ADD COLUMN chunk_offsets_ms TEXT DEFAULT '[]'")
        if "metadata" not in existing_columns:
            conn.execute("ALTER TABLE spans ADD COLUMN metadata TEXT DEFAULT '{}'")

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
            "input_truncated": int(span.input_truncated),
            "output_truncated": int(span.output_truncated),
            "started_at": span.started_at.isoformat(),
            "finished_at": span.finished_at.isoformat() if span.finished_at else None,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "tags": json.dumps(span.tags, ensure_ascii=False),
            "children": json.dumps(span.children, ensure_ascii=False),
            "stream": int(span.stream),
            "ttft_ms": span.ttft_ms,
            "stream_chunks": span.stream_chunks,
            "chunk_offsets_ms": json.dumps(span.chunk_offsets_ms),
            "metadata": json.dumps(span.metadata or {}, ensure_ascii=False, default=str),
        }

    def _deserialize_span(self, row: sqlite3.Row) -> TraceSpan:
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
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
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
        conn.execute("BEGIN IMMEDIATE")
        try:
            data = self._serialize_span(span)

            cursor = conn.execute("SELECT children FROM spans WHERE span_id = ?", (span.span_id,))
            existing = cursor.fetchone()
            if existing:
                existing_children: list[str] = json.loads(existing["children"]) if existing["children"] else []
                for cid in existing_children:
                    if cid not in span.children:
                        span.children.append(cid)

            cursor = conn.execute("SELECT span_id FROM spans WHERE parent_id = ?", (span.span_id,))
            for row in cursor.fetchall():
                cid = row["span_id"]
                if cid not in span.children:
                    span.children.append(cid)

            data["children"] = json.dumps(span.children, ensure_ascii=False)

            conn.execute(
                """
                INSERT OR REPLACE INTO spans (
                    span_id, trace_id, parent_id, agent, model,
                    input, output, error,
                    tokens_input, tokens_output, cost_usd,
                    input_truncated, output_truncated,
                    started_at, finished_at, duration_ms,
                    status, tags, children,
                    stream, ttft_ms, stream_chunks, chunk_offsets_ms, metadata
                ) VALUES (
                    :span_id, :trace_id, :parent_id, :agent, :model,
                    :input, :output, :error,
                    :tokens_input, :tokens_output, :cost_usd,
                    :input_truncated, :output_truncated,
                    :started_at, :finished_at, :duration_ms,
                    :status, :tags, :children,
                    :stream, :ttft_ms, :stream_chunks, :chunk_offsets_ms, :metadata
                )
            """,
                data,
            )

            if span.parent_id:
                cursor = conn.execute("SELECT children FROM spans WHERE span_id = ?", (span.parent_id,))
                row = cursor.fetchone()
                if row:
                    children: list[str] = json.loads(row["children"]) if row["children"] else []
                    if span.span_id not in children:
                        children.append(span.span_id)
                        conn.execute(
                            "UPDATE spans SET children = ? WHERE span_id = ?",
                            (json.dumps(children, ensure_ascii=False), span.parent_id),
                        )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at", (trace_id,))
        return [self._deserialize_span(row) for row in cursor.fetchall()]

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,))
        row = cursor.fetchone()
        return self._deserialize_span(row) if row else None

    def list_traces(self, limit: int = 10, offset: int = 0) -> list[str]:
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT trace_id FROM spans
            GROUP BY trace_id
            ORDER BY MAX(started_at) DESC
            LIMIT ? OFFSET ?
        """,
            (limit, offset),
        )
        return [row["trace_id"] for row in cursor.fetchall()]

    def get_last_trace_id(self) -> Optional[str]:
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT trace_id FROM spans
            ORDER BY started_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        return row["trace_id"] if row else None

    def query(
        self,
        trace_id: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        since: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> list[TraceSpan]:
        conditions: list[str] = []
        params: list[Any] = []

        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if min_duration_ms is not None:
            conditions.append("duration_ms >= ?")
            params.append(min_duration_ms)
        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if metadata:
            for key, value in metadata.items():
                conditions.append("json_extract(metadata, ?) = ?")
                params.extend([_metadata_json_path(key), value])

        where = " AND ".join(conditions) if conditions else "1=1"
        conn = self._get_connection()
        cursor = conn.execute(f"SELECT * FROM spans WHERE {where} ORDER BY started_at", params)
        return [self._deserialize_span(row) for row in cursor.fetchall()]

    def clear(self) -> None:
        conn = self._get_connection()
        conn.execute("DELETE FROM spans")

    def close(self) -> None:
        with self._conn_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
