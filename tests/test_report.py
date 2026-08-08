import json

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
    assert "Span Details" in result


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
