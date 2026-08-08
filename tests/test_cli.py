import json

import pytest
from typer.testing import CliRunner

from traceforge import trace
from traceforge.cli import _percentile, app
from traceforge.collector.memory import MemoryCollector
from traceforge.decorator import set_default_collector


def test_percentile_basic():
    values = [10, 20, 30, 40]
    assert _percentile(values, 0.95) == 40
    assert _percentile(values, 0.5) == 20


def test_percentile_single_value():
    assert _percentile([100], 0.95) == 100


def test_percentile_empty():
    assert _percentile([], 0.95) == 0


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def populated_collector():
    collector = MemoryCollector()
    set_default_collector(collector)

    @trace(agent="root", collector=collector)
    def root():
        return child()

    @trace(agent="child", model="gemini-2.5-flash", collector=collector)
    def child():
        return "done"

    root()
    return collector


def test_cli_help(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "TraceForge" in result.output


def test_cli_list(runner, populated_collector):
    result = runner.invoke(app, ["list", "--last", "5"])
    assert result.exit_code == 0
    assert "root" not in result.output  # list shows trace IDs, not agents
    trace_ids = populated_collector.list_traces()
    assert trace_ids[0][:8] in result.output


def test_cli_show(runner, populated_collector):
    trace_id = populated_collector.get_last_trace_id()
    result = runner.invoke(app, ["show", trace_id])
    assert result.exit_code == 0
    assert "root" in result.output
    assert "child" in result.output
    assert "gemini-2.5-flash" in result.output


def test_cli_show_not_found(runner):
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_stats(runner, populated_collector):
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "root" in result.output
    assert "child" in result.output


def test_cli_report_json(runner, populated_collector, tmp_path):
    trace_id = populated_collector.get_last_trace_id()
    out = str(tmp_path / "report.json")
    result = runner.invoke(app, ["report", trace_id, "--output", out, "--format", "json"])
    assert result.exit_code == 0
    assert "saved" in result.output.lower()
    with open(out) as f:
        data = json.load(f)
    assert data["total_spans"] == 2


def test_cli_report_html(runner, populated_collector, tmp_path):
    trace_id = populated_collector.get_last_trace_id()
    out = str(tmp_path / "report.html")
    result = runner.invoke(app, ["report", trace_id, "--output", out])
    assert result.exit_code == 0
    assert "saved" in result.output.lower()
    with open(out) as f:
        content = f.read()
    assert "TraceForge" in content


def test_cli_report_not_found(runner):
    result = runner.invoke(app, ["report", "nonexistent", "--output", "/tmp/r.html"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_export_json(runner, populated_collector):
    result = runner.invoke(app, ["export", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    agents = {d["agent"] for d in data}
    assert agents == {"root", "child"}


def test_cli_export_json_with_trace_id(runner, populated_collector):
    trace_id = populated_collector.get_last_trace_id()
    result = runner.invoke(app, ["export", "--format", "json", "--trace-id", trace_id])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
