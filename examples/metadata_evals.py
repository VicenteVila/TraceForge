"""Semantic metadata + failure attribution + self-contained evals.

Shows the EvoForge-ready pattern: tag spans with component / archetype /
input_sources, attribute failures by component, and evaluate spans carrying
their own golden reference in metadata.
"""

import traceforge
from traceforge.evals import run_evals, summary

traceforge.configure(collector="memory")


@traceforge.trace(agent="planner", metadata={"component": "planner", "archetype": "landing-page"})
def plan(task: str) -> str:
    if "fail" in task:
        raise ValueError("cannot plan a failing task")
    return f"plan for: {task}"


@traceforge.trace(agent="developer", metadata={"component": "developer", "archetype": "landing-page"})
def code(task: str) -> str:
    return f"code for: {task}"


if __name__ == "__main__":
    # Inherited context: every span below shares run_id + input_sources
    prev, token = traceforge.set_metadata_context(
        run_id="run-001",
        input_sources=["user_task", "archetype_yaml", "retrieved_skill"],
    )
    try:
        for task in ["build hero", "fail this one", "build footer"]:
            try:
                result = plan(task)
                code(f"{result} + step")
            except Exception as e:
                print(f"  task '{task}' -> {type(e).__name__}")
    finally:
        traceforge.reset_metadata_context(token)

    # Attribute failures per component (EvoForge digestor pattern)
    failures = traceforge.query(status="error", metadata={"component": "planner"})
    print(f"\n{len(failures)} planner failures")

    # Show inherited metadata on a span
    ok = traceforge.query(agent="developer")
    print("developer span metadata ->", ok[0].metadata)

    # Self-contained evals on an explicit collector, segmented by archetype
    from traceforge.collector.memory import MemoryCollector

    evals_col = MemoryCollector()
    with traceforge.span(
        "writer",
        collector=evals_col,
        metadata={"reference": "the fox jumps over the dog", "archetype": "ecommerce"},
    ):
        pass
    with traceforge.span(
        "writer",
        collector=evals_col,
        metadata={"reference": "the fox jumps over the dog", "archetype": "landing-page"},
    ):
        pass

    res = run_evals(evals_col)
    factuality = [r for r in res if r.name == "factuality"]
    print(f"\n{len(factuality)} factuality checks (self-contained references)")
    segments = summary(run_evals(evals_col, group_by="archetype"))
    for name, groups in segments.items():
        for group, m in groups.items():
            print(f"  {name} {group}: avg {m['avg_score']:.2f} (n={m['count']})")
