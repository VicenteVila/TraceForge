import traceforge

traceforge.configure(collector="memory")

@traceforge.trace(agent="greeter", model="mock-1.0", tags=["example"])
def greet(name: str) -> str:
    return f"Hello, {name}!"

@traceforge.trace(agent="formatter", model="mock-1.0")
def format_message(greeting: str) -> str:
    return greeting.upper()

@traceforge.trace(agent="pipeline", model=None)
def run_pipeline(name: str) -> str:
    msg = greet(name)
    return format_message(msg)


if __name__ == "__main__":
    result = run_pipeline("TraceForge")
    print(f"Result: {result}")

    trace_id = traceforge.get_last_trace_id()
    print(f"\nTrace ID: {trace_id}")

    spans = traceforge.query(trace_id=trace_id)
    for s in spans:
        print(f"  [{s.agent}] {s.duration_ms}ms - {s.status}")

    traceforge.show(trace_id)
