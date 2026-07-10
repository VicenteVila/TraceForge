"""
Example: Async multi-agent pipeline with concurrent LLM calls.

Run with:   python examples/async_multi_agent.py

Demonstrates:
  - async @trace decorators
  - Nested traces across concurrent asyncio.gather workers
  - Error handling with fallback agent
  - Rich trace tree output
"""
import asyncio
import random

import traceforge

traceforge.configure(collector="memory")


def mock_llm(agent: str, prompt: str) -> str:
    """Simulate an LLM call with random latency."""
    import time
    time.sleep(random.uniform(0.005, 0.02))
    with traceforge.span(agent=f"{agent}_llm", tags=["llm_call"]) as sp:
        sp.set_tokens(input=len(prompt), output=len(prompt) // 2)
    return f"{agent} result for: {prompt[:40]}"


@traceforge.trace(agent="researcher", tags=["async"])
async def research(topic: str) -> str:
    await asyncio.sleep(random.uniform(0.01, 0.03))
    return mock_llm("researcher", f"Research {topic}")


@traceforge.trace(agent="writer", tags=["async"])
async def write_article(findings: str) -> str:
    await asyncio.sleep(random.uniform(0.01, 0.02))
    return mock_llm("writer", f"Write article based on: {findings}")


@traceforge.trace(agent="reviewer", tags=["async"])
async def review_article(draft: str) -> str:
    await asyncio.sleep(random.uniform(0.005, 0.015))
    if random.random() < 0.3:
        raise ValueError("Review failed: quality threshold not met")
    return mock_llm("reviewer", f"Review and polish: {draft}")


@traceforge.trace(agent="editor", tags=["async"])
async def editor(topic: str, retries: int = 2) -> dict:
    findings = await research(topic)
    draft = await write_article(findings)

    for attempt in range(retries):
        try:
            final = await review_article(draft)
            return {"topic": topic, "status": "published", "content": final}
        except ValueError:
            if attempt == retries - 1:
                raise
            print(f"  Review failed (attempt {attempt + 1}), retrying...")
            draft = await write_article(f"{findings} (revised, attempt {attempt + 2})")

    return {"topic": topic, "status": "failed"}


@traceforge.trace(agent="orchestrator", model=None)
async def run_multi_topic(topics: list[str]) -> list[dict]:
    tasks = [editor(topic) for topic in topics]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output = []
    for topic, result in zip(topics, results):
        if isinstance(result, Exception):
            output.append({"topic": topic, "status": "failed", "error": str(result)})
        else:
            output.append(result)
    return output


if __name__ == "__main__":
    topics = ["TraceForge library", "Async tracing patterns", "Agent orchestration"]

    print("Running async multi-agent pipeline...\n")
    results = asyncio.run(run_multi_topic(topics))

    for r in results:
        print(f"  [{r['topic']}] {r['status']}")
        if r.get("error"):
            print(f"    Error: {r['error']}")

    last_id = traceforge.get_last_trace_id()
    if last_id:
        print("\nLast trace tree:")
        traceforge.show(last_id)

    print(f"\nAll traces ({len(traceforge.list_traces())} total):")
    for tid in traceforge.list_traces():
        spans = traceforge.query(trace_id=tid)
        agents = [s.agent for s in spans]
        print(f"  {tid[:8]}... -> {agents}")
