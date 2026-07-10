import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

MAX_INPUT_LEN = 2000
MAX_OUTPUT_LEN = 5000


def _calculate_cost(model: str | None, tokens_input: int = 0, tokens_output: int = 0) -> float:
    try:
        from .pricing import calculate_cost
        return calculate_cost(model, tokens_input, tokens_output)
    except ImportError:
        return 0.0


class TraceSpan(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    span_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None

    agent: str
    model: Optional[str] = None

    input: Any = None
    output: Any = None
    error: Optional[str] = None

    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0

    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_ms: int = 0

    status: str = "ok"
    tags: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)

    def close(self):
        self.finished_at = datetime.now()
        self.duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)
        self.cost_usd = _calculate_cost(self.model, self.tokens_input, self.tokens_output)

    def set_output(self, value: Any):
        self.output = _capture_output(value)

    def set_error(self, error: str):
        self.error = error
        self.status = "error"

    def set_tokens(self, input: int, output: int):
        self.tokens_input = input
        self.tokens_output = output


class TraceCollector:
    def save(self, span: TraceSpan) -> None:
        raise NotImplementedError

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        raise NotImplementedError

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        raise NotImplementedError

    def list_traces(self, limit: int = 10) -> list[str]:
        raise NotImplementedError

    def get_last_trace_id(self) -> Optional[str]:
        raise NotImplementedError

    def query(
        self,
        trace_id: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        since: Optional[datetime] = None,
    ) -> list[TraceSpan]:
        raise NotImplementedError


def _truncate(obj: Any, max_len: int = MAX_INPUT_LEN) -> Any:
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + f"... [truncated, {len(obj)} total]"
    if isinstance(obj, (list, tuple)) and len(obj) > 10:
        return list(obj[:10]) + [f"... ({len(obj)}) total"]
    if isinstance(obj, dict):
        return {k: _truncate(v, max_len) for k, v in obj.items()}
    return obj


def _capture_input(args: tuple, kwargs: dict) -> dict:
    return {"args": _truncate(args), "kwargs": _truncate(kwargs)}


def _capture_output(result: Any) -> Any:
    try:
        return _truncate(result, MAX_OUTPUT_LEN)
    except Exception:
        return f"<unserializable: {type(result).__name__}>"
