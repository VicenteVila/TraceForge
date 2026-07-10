"""
Example: Tracing FastAPI endpoints with TraceForge.

Install:    pip install "traceforge[fastapi]"
Run with:   uvicorn examples.fastapi_integration:app --reload
Test:       curl http://localhost:8000/chat?msg=hello

For demo purposes, this uses mocked LLM responses.
"""
import traceforge
from traceforge import span, trace

traceforge.configure(collector="sqlite", db_path="fastapi_traces.db")

try:
    from fastapi import FastAPI, Query
    from fastapi.responses import JSONResponse
except ImportError:
    msg = "fastapi not installed. Install with: pip install fastapi uvicorn"
    raise ImportError(msg)

app = FastAPI(title="TraceForge + FastAPI Demo")


def mock_llm_call(prompt: str, model: str = "gpt-4o") -> str:
    with span(agent="llm_request", model=model, tags=["llm_call"]) as sp:
        tokens = max(10, len(prompt) // 2)
        sp.set_tokens(input=tokens, output=tokens // 2)
    return f"Echo: {prompt[:60]}"


@app.get("/chat")
@trace(agent="/chat", model=None, tags=["endpoint"])
async def chat_endpoint(msg: str = Query("hello")):
    response = mock_llm_call(msg)
    trace_id = traceforge.get_last_trace_id()
    return JSONResponse({
        "response": response,
        "trace_id": trace_id,
    })


@app.get("/pipeline")
@trace(agent="/pipeline", model=None, tags=["endpoint"])
async def pipeline_endpoint(topic: str = Query("AI")):
    step1 = mock_llm_call(f"Research {topic}", "gpt-4o")
    step2 = mock_llm_call(f"Summarize: {step1}", "gpt-4o-mini")
    trace_id = traceforge.get_last_trace_id()
    return JSONResponse({
        "research": step1,
        "summary": step2,
        "trace_id": trace_id,
    })


@app.get("/traces")
async def list_traces():
    trace_ids = traceforge.list_traces(limit=10)
    return JSONResponse({"traces": trace_ids})


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    spans = traceforge.query(trace_id=trace_id)
    if not spans:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "trace_id": trace_id,
        "spans": [{
            "span_id": s.span_id,
            "agent": s.agent,
            "model": s.model,
            "status": s.status,
            "duration_ms": s.duration_ms,
            "tokens_input": s.tokens_input,
            "tokens_output": s.tokens_output,
        } for s in spans],
    })
