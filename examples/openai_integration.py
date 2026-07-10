"""
Example: Instrumenting OpenAI API calls with TraceForge.

Run with:   python examples/openai_integration.py

Uses mocked API calls so it works without an API key.
For real usage, replace mock_openai_chat with openai.OpenAI().chat.completions.create()
"""
import traceforge

traceforge.configure(collector="memory")


def fake_openai_chat(model: str, messages: list) -> tuple[str, int, int]:
    """Simulate an OpenAI API call returning content + token counts."""
    prompt = messages[-1]["content"] if messages else ""
    response = f"Response to: {prompt[:50]}..."
    input_tokens = len(prompt.split())
    output_tokens = len(response.split())
    return response, input_tokens, output_tokens


@traceforge.trace(agent="llm", model="gpt-4o", tags=["openai", "chat"])
def call_llm(prompt: str) -> str:
    response, inp_tok, out_tok = fake_openai_chat("gpt-4o", [
        {"role": "user", "content": prompt},
    ])
    tf = traceforge
    with tf.span(agent="llm_request", model="gpt-4o", tags=["llm_call"]) as sp:
        sp.set_tokens(input=inp_tok, output=out_tok)
    return response


@traceforge.trace(agent="summarizer", model="gpt-4o-mini", tags=["openai"])
def summarize(text: str) -> str:
    response, inp_tok, out_tok = fake_openai_chat("gpt-4o-mini", [
        {"role": "system", "content": "Summarize this."},
        {"role": "user", "content": text},
    ])
    tf = traceforge
    with tf.span(agent="summary_llm", model="gpt-4o-mini", tags=["llm_call"]) as sp:
        sp.set_tokens(input=inp_tok, output=out_tok)
    return response


@traceforge.trace(agent="pipeline", model=None)
def run_pipeline(topic: str) -> dict:
    initial = call_llm(f"Write about {topic}")
    summary = summarize(initial)
    return {"full": initial, "summary": summary}


if __name__ == "__main__":
    result = run_pipeline("TraceForge")
    print(f"Summary: {result['summary']}")

    trace_id = traceforge.get_last_trace_id()
    print("\nTrace tree:")
    traceforge.show(trace_id)
