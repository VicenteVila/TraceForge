import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core import TraceCollector, TraceSpan


class SQLiteCollector(TraceCollector):
    def __init__(self, db_path: str = "traces.db"):
        self._db_path = str(Path(db_path).resolve())
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()
        self._last_trace_id: Optional[str] = None

    def _create_schema(self) -> None:
        self._conn.executescript("""
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
                children TEXT DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
            CREATE INDEX IF NOT EXISTS idx_spans_agent ON spans(agent);
            CREATE INDEX IF NOT EXISTS idx_spans_status ON spans(status);
            CREATE INDEX IF NOT EXISTS idx_spans_started_at ON spans(started_at);
        """)
        self._conn.commit()

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
            "started_at": span.started_at.isoformat(),
            "finished_at": span.finished_at.isoformat() if span.finished_at else None,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "tags": json.dumps(span.tags, ensure_ascii=False),
            "children": json.dumps(span.children, ensure_ascii=False),
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
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            duration_ms=row["duration_ms"],
            status=row["status"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            children=json.loads(row["children"]) if row["children"] else [],
        )

    def save(self, span: TraceSpan) -> None:
        data = self._serialize_span(span)

        cursor = self._conn.execute(
            "SELECT children FROM spans WHERE span_id = ?", (span.span_id,)
        )
        existing = cursor.fetchone()
        if existing:
            existing_children: list[str] = json.loads(existing["children"]) if existing["children"] else []
            for cid in existing_children:
                if cid not in span.children:
                    span.children.append(cid)

        data["children"] = json.dumps(span.children, ensure_ascii=False)

        self._conn.execute("""
            INSERT OR REPLACE INTO spans (
                span_id, trace_id, parent_id, agent, model,
                input, output, error,
                tokens_input, tokens_output, cost_usd,
                started_at, finished_at, duration_ms,
                status, tags, children
            ) VALUES (
                :span_id, :trace_id, :parent_id, :agent, :model,
                :input, :output, :error,
                :tokens_input, :tokens_output, :cost_usd,
                :started_at, :finished_at, :duration_ms,
                :status, :tags, :children
            )
        """, data)
        self._conn.commit()
        self._last_trace_id = span.trace_id

        if span.parent_id:
            cursor = self._conn.execute(
                "SELECT children FROM spans WHERE span_id = ?", (span.parent_id,)
            )
            row = cursor.fetchone()
            if row:
                children: list[str] = json.loads(row["children"]) if row["children"] else []
                if span.span_id not in children:
                    children.append(span.span_id)
                    self._conn.execute(
                        "UPDATE spans SET children = ? WHERE span_id = ?",
                        (json.dumps(children, ensure_ascii=False), span.parent_id)
                    )
                    self._conn.commit()

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        cursor = self._conn.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at",
            (trace_id,)
        )
        return [self._deserialize_span(row) for row in cursor.fetchall()]

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        cursor = self._conn.execute(
            "SELECT * FROM spans WHERE span_id = ?", (span_id,)
        )
        row = cursor.fetchone()
        return self._deserialize_span(row) if row else None

    def list_traces(self, limit: int = 10) -> list[str]:
        cursor = self._conn.execute("""
            SELECT trace_id FROM spans
            GROUP BY trace_id
            ORDER BY MAX(started_at) DESC
            LIMIT ?
        """, (limit,))
        return [row["trace_id"] for row in cursor.fetchall()]

    def get_last_trace_id(self) -> Optional[str]:
        cursor = self._conn.execute("""
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

        where = " AND ".join(conditions) if conditions else "1=1"
        cursor = self._conn.execute(
            f"SELECT * FROM spans WHERE {where} ORDER BY started_at",
            params
        )
        return [self._deserialize_span(row) for row in cursor.fetchall()]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM spans")
        self._conn.commit()
        self._last_trace_id = None

    def close(self) -> None:
        self._conn.close()
