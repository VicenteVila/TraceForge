import uuid
import warnings
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .redact import redact_value

MAX_INPUT_LEN = 30000
MAX_OUTPUT_LEN = 80000
MAX_LIST_ITEMS = 50

_truncation_limits: dict[str, int] = {
    "input": MAX_INPUT_LEN,
    "output": MAX_OUTPUT_LEN,
    "list_items": MAX_LIST_ITEMS,
}


def set_truncation_limits(
    max_input_len: Optional[int] = None,
    max_output_len: Optional[int] = None,
    max_list_items: Optional[int] = None,
) -> None:
    """Configure capture limits. A value of 0 disables truncation for that dimension."""
    if max_input_len is not None:
        if max_input_len < 0:
            raise ValueError("max_input_len must be >= 0")
        _truncation_limits["input"] = max_input_len
    if max_output_len is not None:
        if max_output_len < 0:
            raise ValueError("max_output_len must be >= 0")
        _truncation_limits["output"] = max_output_len
    if max_list_items is not None:
        if max_list_items < 0:
            raise ValueError("max_list_items must be >= 0")
        _truncation_limits["list_items"] = max_list_items


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

    input_truncated: bool = False
    output_truncated: bool = False

    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0

    stream: bool = False
    ttft_ms: Optional[float] = None
    stream_chunks: int = 0
    chunk_offsets_ms: list[float] = Field(default_factory=list)

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
        captured, truncated = _capture_output(value)
        self.output = captured
        self.output_truncated = truncated

    def set_input(self, value: Any):
        max_len = _truncation_limits["input"]
        max_items = _truncation_limits["list_items"]
        try:
            captured, truncated = _truncate(value, max_len, max_items)
        except Exception:
            captured, truncated = f"<unserializable: {type(value).__name__}>", False
        masked, _ = redact_value(captured)
        self.input = masked
        self.input_truncated = truncated

    def set_error(self, error: str):
        self.error = error
        self.status = "error"

    def set_tokens(self, input: int, output: int):
        self.tokens_input = input
        self.tokens_output = output

    @property
    def throughput_tps(self) -> float:
        """Output tokens per second, derived from the streaming duration."""
        if self.duration_ms > 0:
            return self.tokens_output / (self.duration_ms / 1000.0)
        return 0.0


class TraceCollector:
    def save(self, span: TraceSpan) -> None:
        raise NotImplementedError

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        raise NotImplementedError

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        raise NotImplementedError

    def list_traces(self, limit: int = 10, offset: int = 0) -> list[str]:
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


def _truncate(obj: Any, max_len: int, max_list_items: int) -> tuple[Any, bool]:
    """Truncate obj to the configured limits, returning (value, truncated)."""
    if max_len > 0 and isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + f"... [truncated, {len(obj)} total]", True

    if isinstance(obj, (list, tuple)):
        if max_list_items > 0 and len(obj) > max_list_items:
            items = list(obj[:max_list_items])
            truncated = True
            note = f"... ({len(obj)}) total"
        else:
            items = list(obj)
            truncated = False
            note = None
        kept: list[Any] = []
        for item in items:
            value, t = _truncate(item, max_len, max_list_items)
            kept.append(value)
            truncated = truncated or t
        if note is not None:
            kept.append(note)
        if isinstance(obj, tuple):
            return tuple(kept), truncated
        return kept, truncated

    if isinstance(obj, dict):
        result = {}
        truncated = False
        for k, v in obj.items():
            result[k], t = _truncate(v, max_len, max_list_items)
            truncated = truncated or t
        return result, truncated
    return obj, False


def _truncation_warning(label: str) -> None:
    max_len = _truncation_limits["output" if label == "output" else "input"]
    max_items = _truncation_limits["list_items"]
    warnings.warn(
        f"TraceForge truncated captured {label} "
        f"(max {max_len if max_len else 'unlimited'} chars, "
        f"{max_items if max_items else 'unlimited'} list items). "
        f"Data was lost; raise limits via traceforge.set_truncation_limits() "
        f"or inspect the span's {label}_truncated flag.",
        RuntimeWarning,
        stacklevel=2,
    )


def _capture_input(args: tuple, kwargs: dict) -> tuple[dict, bool]:
    max_len = _truncation_limits["input"]
    max_items = _truncation_limits["list_items"]
    args_value, args_truncated = _truncate(args, max_len, max_items)
    kwargs_value, kwargs_truncated = _truncate(kwargs, max_len, max_items)
    truncated = args_truncated or kwargs_truncated
    if truncated:
        _truncation_warning("input")
    value = {"args": args_value, "kwargs": kwargs_value}
    masked, _ = redact_value(value)
    return masked, truncated


def _capture_output(result: Any) -> tuple[Any, bool]:
    max_len = _truncation_limits["output"]
    max_items = _truncation_limits["list_items"]
    try:
        value, truncated = _truncate(result, max_len, max_items)
    except Exception:
        return f"<unserializable: {type(result).__name__}>", False
    if truncated:
        _truncation_warning("output")
    masked, _ = redact_value(value)
    return masked, truncated
