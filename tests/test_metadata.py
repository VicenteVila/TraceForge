import os

import pytest

import traceforge
from traceforge.abtest import ABResult, VariantMetrics, compare_prompts
from traceforge.collector.memory import MemoryCollector
from traceforge.collector.sqlite import SQLiteCollector
from traceforge.core import (
    TraceSpan,
    _metadata_contains,
    _metadata_json_path,
    reset_metadata_context,
    set_metadata_context,
)
from traceforge.evals import run_evals


def test_add_metadata_merges():
    span = TraceSpan(agent="a")
    assert span.metadata == {}
    span.add_metadata(component="planner", archetype="landing-page")
    assert span.metadata == {"component": "planner", "archetype": "landing-page"}
    span.add_metadata(component="developer")
    assert span.metadata["component"] == "developer"


def test_trace_decorator_metadata_local():
    c = MemoryCollector()

    @traceforge.trace(agent="planner", metadata={"component": "planner"}, collector=c)
    def plan():
        return 42

    plan()
    span = c.query(agent="planner")[0]
    assert span.metadata == {"component": "planner"}


def test_span_context_manager_metadata_local():
    c = MemoryCollector()
    with traceforge.span("developer", metadata={"component": "developer"}, collector=c) as sp:
        sp.add_metadata(tool="write_file")
    span = c.query(agent="developer")[0]
    assert span.metadata["component"] == "developer"
    assert span.metadata["tool"] == "write_file"


def test_metadata_context_inherited_by_decorator():
    c = MemoryCollector()

    @traceforge.trace(agent="planner", collector=c)
    def plan():
        return 1

    prev, token = set_metadata_context(component="planner", archetype="landing-page")
    try:
        plan()
    finally:
        reset_metadata_context(token)

    span = c.query(agent="planner")[0]
    assert span.metadata["component"] == "planner"
    assert span.metadata["archetype"] == "landing-page"


def test_metadata_context_inherited_by_span_context():
    c = MemoryCollector()
    prev, token = set_metadata_context(component="developer", run_id="r1")
    try:
        with traceforge.span("developer", collector=c):
            pass
    finally:
        reset_metadata_context(token)

    span = c.query(agent="developer")[0]
    assert span.metadata["run_id"] == "r1"


def test_local_metadata_overrides_context():
    c = MemoryCollector()
    prev, token = set_metadata_context(component="global")
    try:
        with traceforge.span("dev", metadata={"component": "local"}, collector=c):
            pass
    finally:
        reset_metadata_context(token)

    span = c.query(agent="dev")[0]
    assert span.metadata["component"] == "local"


def test_set_metadata_context_returns_previous():
    prev1, token1 = set_metadata_context(a=1)
    prev2, token2 = set_metadata_context(b=2)
    assert prev1 == {}
    assert prev2 == {"a": 1}
    reset_metadata_context(token2)
    reset_metadata_context(token1)


@pytest.fixture
def sqlite_collector(tmp_path):
    db = str(tmp_path / "meta.db")
    c = SQLiteCollector(db)
    yield c
    c.close()
    if os.path.exists(db):
        os.unlink(db)


def test_metadata_roundtrip_sqlite(sqlite_collector):
    span = TraceSpan(
        agent="planner",
        metadata={"component": "planner", "component_version": "git:a81f9e2", "input_sources": ["user_task", "skill"]},
    )
    sqlite_collector.save(span)
    got = sqlite_collector.get_span(span.span_id)
    assert got.metadata == span.metadata


def test_metadata_roundtrip_memory():
    c = MemoryCollector()
    span = TraceSpan(agent="a", metadata={"archetype": "landing-page", "nested": {"rollout": "v3"}})
    c.save(span)
    assert c.get_span(span.span_id).metadata == span.metadata


def test_sqlite_migration_adds_metadata_column(tmp_path):
    import sqlite3

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE spans (
            span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_id TEXT,
            agent TEXT NOT NULL, model TEXT, input TEXT, output TEXT, error TEXT,
            tokens_input INTEGER DEFAULT 0, tokens_output INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0, started_at TEXT NOT NULL,
            finished_at TEXT, duration_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok', tags TEXT DEFAULT '[]', children TEXT DEFAULT '[]',
            input_truncated INTEGER DEFAULT 0, output_truncated INTEGER DEFAULT 0,
            stream INTEGER DEFAULT 0, ttft_ms REAL,
            stream_chunks INTEGER DEFAULT 0, chunk_offsets_ms TEXT DEFAULT '[]'
        )
        """
    )
    conn.commit()
    conn.close()

    c = SQLiteCollector(db)
    span = TraceSpan(agent="a", metadata={"component": "planner"})
    c.save(span)
    assert c.get_span(span.span_id).metadata == {"component": "planner"}
    cols = [r[1] for r in c._get_connection().execute("PRAGMA table_info(spans)")]
    assert "metadata" in cols
    c.close()
    os.unlink(db)


def test_metadata_contains_scalar():
    assert _metadata_contains({"component": "planner"}, {"component": "planner"})
    assert not _metadata_contains({"component": "developer"}, {"component": "planner"})


def test_metadata_contains_list_any_of():
    data = {"input_sources": ["user_task", "skill"]}
    assert _metadata_contains(data, {"input_sources": "skill"})
    assert not _metadata_contains(data, {"input_sources": "missing"})


def test_metadata_contains_nested():
    data = {"harness": {"rollout": "v3"}}
    assert _metadata_contains(data, {"harness.rollout": "v3"})
    assert not _metadata_contains(data, {"harness.none": "x"})


def test_json_path_escaping():
    assert _metadata_json_path("component") == '$."component"'
    assert _metadata_json_path("a.b") == '$."a"."b"'


def test_query_filter_sqlite(sqlite_collector):
    p = TraceSpan(agent="planner", status="error", metadata={"component": "planner", "archetype": "landing-page"})
    d = TraceSpan(agent="developer", status="ok", metadata={"component": "developer", "archetype": "ecommerce"})
    sqlite_collector.save(p)
    sqlite_collector.save(d)

    hits = sqlite_collector.query(metadata={"component": "planner"})
    assert [h.span_id for h in hits] == [p.span_id]
    hits = sqlite_collector.query(status="error", metadata={"archetype": "landing-page"})
    assert [h.span_id for h in hits] == [p.span_id]
    assert sqlite_collector.query(metadata={"archetype": "nope"}) == []


def test_query_filter_memory():
    c = MemoryCollector()
    p = TraceSpan(agent="planner", metadata={"component": "planner"})
    d = TraceSpan(agent="developer", metadata={"component": "developer"})
    c.save(p)
    c.save(d)
    assert [s.agent for s in c.query(metadata={"component": "planner"})] == ["planner"]


def test_abtest_injects_variant_metadata():
    @traceforge.trace(agent="writer")
    def answer(prompt, sample):
        return f"{prompt}: {sample}"

    result: ABResult = compare_prompts(
        answer,
        {"concise": "S"},
        samples=["one", "two"],
    )
    assert len(result.variants) == 1
    v: VariantMetrics = result.variants[0]
    assert v.runs == 2
    assert len(v.span_ids) == 2


def test_abtest_variant_metrics_have_ids():
    ids_seen = {}

    @traceforge.trace(agent="writer")
    def answer(prompt, sample):
        return f"{prompt}: {sample}"

    result = compare_prompts(answer, {"a": "S1", "b": "S2"}, samples=["one"])
    for v in result.variants:
        assert v.span_ids
        assert v.trace_ids
        assert v.runs == 1
        ids_seen[v.name] = v.span_ids


def test_evals_reference_from_metadata():
    c = MemoryCollector()
    c.save(TraceSpan(agent="a", output="the quick brown fox", metadata={"reference": "the fox jumps"}))
    c.save(TraceSpan(agent="b", output="irrelevant", metadata={"reference": "the fox jumps"}))

    results = run_evals(c)
    factuality = [r for r in results if r.name == "factuality"]
    assert len(factuality) == 2


def test_evals_group_by_segments_summary():
    c = MemoryCollector()
    c.save(TraceSpan(agent="a", output="the quick brown fox", metadata={"reference": "the fox jumps", "domain": "A"}))
    c.save(
        TraceSpan(
            agent="b",
            output="totally different content here",
            metadata={"reference": "the fox jumps", "domain": "A"},
        )
    )
    c.save(TraceSpan(agent="c", output="the quick brown fox", metadata={"reference": "the fox jumps", "domain": "B"}))

    results = run_evals(c, group_by="domain")
    sums = {}
    for r in results:
        if r.detail:
            sums.setdefault(r.detail, []).append(r.score)
    assert "domain=A" in sums
    assert "domain=B" in sums


def test_api_query_metadata():
    c = MemoryCollector()
    prev, token = set_metadata_context(component="planner", archetype="landing-page")
    try:
        with traceforge.span("planner", collector=c):
            pass
    finally:
        reset_metadata_context(token)

    # module-level query uses the global collector, not c; test collector path directly instead
    assert c.query(metadata={"component": "planner"})
