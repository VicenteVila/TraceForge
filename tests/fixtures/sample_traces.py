from datetime import datetime

from traceforge.core import TraceSpan


def make_sample_span(
    agent: str,
    model: str | None = None,
    parent_id: str | None = None,
    trace_id: str | None = None,
    status: str = "ok",
    tokens_input: int = 0,
    tokens_output: int = 0,
    duration_ms: int = 100,
    error: str | None = None,
) -> TraceSpan:
    now = datetime.now()
    kwargs = dict(
        agent=agent,
        model=model,
        parent_id=parent_id,
        started_at=now,
        finished_at=now,
        duration_ms=duration_ms,
        status=status,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        error=error,
    )
    if trace_id is not None:
        kwargs["trace_id"] = trace_id
    return TraceSpan(**kwargs)


def make_pipeline_traces(collector):
    root = make_sample_span(agent="orchestrator", duration_ms=1200)
    collector.save(root)

    children_kwargs = [
        dict(agent="scoping", model="gemini-2.5-flash", tokens_input=200, tokens_output=250, duration_ms=300),
        dict(agent="planner", model="llama-3.3-70b", tokens_input=500, tokens_output=700, duration_ms=500),
        dict(agent="developer", model="deepseek-coder:6.7b", tokens_input=1000, tokens_output=800, duration_ms=400),
    ]

    for kwargs in children_kwargs:
        span = make_sample_span(
            parent_id=root.span_id,
            trace_id=root.trace_id,
            **kwargs,
        )
        collector.save(span)

    return root.trace_id
