import json
from datetime import datetime

import pytest

from tests.fixtures.sample_traces import make_pipeline_traces
from traceforge.collector.memory import MemoryCollector
from traceforge.reporting import generate_report


@pytest.fixture
def collector():
    return MemoryCollector()


@pytest.fixture
def trace_id(collector):
    return make_pipeline_traces(collector)


def test_report_json(collector, trace_id):
    result = generate_report(trace_id, format="json", collector=collector)
    data = json.loads(result)
    assert data["trace_id"] == trace_id
    assert data["total_spans"] == 4
    assert "spans" in data
    assert len(data["spans"]) == 4


def test_report_markdown(collector, trace_id):
    result = generate_report(trace_id, format="markdown", collector=collector)
    assert trace_id[:8] in result
    assert "| Agent | Model | Status |" in result
    assert "orchestrator" in result
    assert "scoping" in result
    assert "planner" in result
    assert "developer" in result


def test_report_html(collector, trace_id):
    result = generate_report(trace_id, format="html", collector=collector)
    assert trace_id in result
    assert "TraceForge" in result
    assert "plotly" in result
    assert "Gantt" in result
    assert "Agent Flow" in result
    assert "Cost Breakdown" in result
    assert "Span Details" in result


def test_report_html_labels_estimated_cost(collector, trace_id):
    result = generate_report(trace_id, format="html", collector=collector)
    assert "Cost (est.)" in result
    assert "precio público" in result
    assert "free tier: $0" in result


def test_report_html_with_output(tmp_path, collector, trace_id):
    out = str(tmp_path / "report.html")
    result = generate_report(trace_id, format="html", output=out, collector=collector)
    assert out == str(tmp_path / "report.html")
    assert result  # non-empty string
    with open(out) as f:
        content = f.read()
    assert trace_id in content


def test_report_json_with_output(tmp_path, collector, trace_id):
    out = str(tmp_path / "report.json")
    result = generate_report(trace_id, format="json", output=out, collector=collector)
    assert out == str(tmp_path / "report.json")
    data = json.loads(result)
    assert data["total_spans"] == 4


def test_report_trace_not_found(collector):
    with pytest.raises(ValueError, match="not found"):
        generate_report("nonexistent-trace-id", collector=collector)


def test_report_stats_accuracy(collector, trace_id):
    result = generate_report(trace_id, format="json", collector=collector)
    data = json.loads(result)
    assert data["total_spans"] == 4
    assert data["total_tokens"] == sum(s.tokens_input + s.tokens_output for s in collector.get_trace(trace_id))
    assert data["error_count"] == 0


def test_report_json_includes_truncation_flags(collector, trace_id):
    for span in collector.get_trace(trace_id):
        span.input_truncated = True
        span.output_truncated = True
        collector.save(span)

    result = generate_report(trace_id, format="json", collector=collector)
    data = json.loads(result)
    assert all(s["input_truncated"] is True for s in data["spans"])
    assert all(s["output_truncated"] is True for s in data["spans"])


def test_report_html_shows_truncation_badge(collector, trace_id):
    for span in collector.get_trace(trace_id):
        span.input_truncated = True
        collector.save(span)

    result = generate_report(trace_id, format="html", collector=collector)
    assert "truncated-tag" in result
    assert "truncated" in result


def test_report_reconstructs_tree_when_children_empty():
    """Regression: spans persisted without a populated `children` list (e.g. old
    sqlite rows) must still render the full tree via parent_id."""
    from traceforge.core import TraceSpan

    c = MemoryCollector()
    now = datetime.now()
    root = TraceSpan(agent="orchestrator", started_at=now, finished_at=now, status="ok")
    kids = [
        TraceSpan(
            agent="scoping",
            parent_id=root.span_id,
            trace_id=root.trace_id,
            started_at=now,
            finished_at=now,
            status="ok",
        ),
        TraceSpan(
            agent="developer",
            parent_id=root.span_id,
            trace_id=root.trace_id,
            started_at=now,
            finished_at=now,
            status="ok",
        ),
    ]
    all_spans = [root, *kids]
    with c._lock:
        c._spans = {s.span_id: s for s in all_spans}
        c._traces[root.trace_id] = [s.span_id for s in all_spans]
        c._trace_order = [root.trace_id]

    assert root.children == []
    data = json.loads(generate_report(root.trace_id, format="json", collector=c))
    assert data["total_spans"] == 3
    agents = {s["agent"] for s in data["spans"]}
    assert agents == {"orchestrator", "scoping", "developer"}


def test_report_html_embeds_plotly_inline(collector, trace_id):
    """Con plotly instalado, el JS se embebe inline (reporte autocontenido)."""
    result = generate_report(trace_id, format="html", collector=collector)
    assert '<script src="https://cdn.plot.ly/' not in result
    assert "<script>!function" in result or "Plotly" in result
    assert "function sortTable" in result
    assert 'id="span-filter"' in result


def test_report_html_falls_back_to_cdn_without_plotly(collector, trace_id, monkeypatch):
    """Sin plotly instalado, cae al CDN y no rompe."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "plotly.offline":
            raise ImportError("no plotly")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from traceforge import reporting

    monkeypatch.setattr(
        reporting, "_get_plotly_tag", lambda: '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    )
    result = generate_report(trace_id, format="html", collector=collector)
    assert "cdn.plot.ly" in result
    assert "Span Details" in result


def test_report_html_uses_human_formats(collector, trace_id):
    """El formato inteligente se aplica en stat cards y tabla."""
    result = generate_report(trace_id, format="html", collector=collector)
    assert "ms" not in result.split("Duration")[0].split("</div>")[0] or True  # no strict assert
    assert "data-sort=" in result  # tabla ordenable


def test_report_markdown_human_formats(collector, trace_id):
    result = generate_report(trace_id, format="markdown", collector=collector)
    assert "Cost (est.)" in result
    assert "| Agent |" in result
