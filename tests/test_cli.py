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


def test_cli_list_json(runner, populated_collector):
    result = runner.invoke(app, ["list", "--last", "5", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["spans"] == 2
    assert data[0]["trace_id"] == populated_collector.get_last_trace_id()


def test_cli_show_json(runner, populated_collector):
    trace_id = populated_collector.get_last_trace_id()
    result = runner.invoke(app, ["show", trace_id, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert {d["agent"] for d in data} == {"root", "child"}
    assert "gemini-2.5-flash" in [d["model"] for d in data]


def test_cli_stats_json(runner, populated_collector):
    result = runner.invoke(app, ["stats", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {d["agent"] for d in data} == {"root", "child"}
    assert data[0]["spans"] == 1


def test_cli_query_filter(runner, populated_collector):
    result = runner.invoke(app, ["query", "--agent", "child"])
    assert result.exit_code == 0
    assert "child" in result.output
    assert "root" not in result.output


def test_cli_query_no_match(runner, populated_collector):
    result = runner.invoke(app, ["query", "--agent", "ghost"])
    assert result.exit_code == 0
    assert "No spans match" in result.output


def test_cli_query_json(runner, populated_collector):
    result = runner.invoke(app, ["query", "--agent", "child", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["agent"] == "child"


def test_cli_clear_requires_confirmation(runner, populated_collector):
    result = runner.invoke(app, ["clear"])
    assert result.exit_code != 0
    assert "confirm" in result.output.lower()
    assert len(populated_collector.query()) == 2


def test_cli_clear(runner, populated_collector):
    result = runner.invoke(app, ["clear", "--yes"])
    assert result.exit_code == 0
    assert populated_collector.query() == []


def test_cli_backend_memory_option(runner):
    result = runner.invoke(app, ["--collector", "memory", "list"])
    assert result.exit_code == 0
    assert "No traces found" in result.output


def test_cli_backend_sqlite_db_path(runner, tmp_path):
    db = str(tmp_path / "cli.db")
    result = runner.invoke(app, ["--collector", "sqlite", "--db-path", db, "list", "--json"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"
    assert (tmp_path / "cli.db").exists()


def test_cli_refresh_prices(runner, tmp_path):
    src = tmp_path / "prices.json"
    src.write_text(
        json.dumps(
            {
                "cli-model": {
                    "input_cost_per_token": 0.000002,
                    "output_cost_per_token": 0.000008,
                },
            }
        )
    )
    cache = str(tmp_path / "cache.json")
    result = runner.invoke(app, ["refresh-prices", "--url", src.as_uri(), "--cache-path", cache])
    assert result.exit_code == 0
    assert "1 modelos cargados" in result.output
    assert (tmp_path / "cache.json").exists()


def test_cli_refresh_prices_reports_change(runner, tmp_path):
    src = tmp_path / "prices.json"
    src.write_text(
        json.dumps(
            {
                "gpt-oss-120b": {
                    "input_cost_per_token": 0.00000040,
                    "output_cost_per_token": 0.00000090,
                },
            }
        )
    )
    cache = str(tmp_path / "cache.json")
    result = runner.invoke(app, ["refresh-prices", "--url", src.as_uri(), "--cache-path", cache])
    assert result.exit_code == 0
    assert "gpt-oss-120b" in result.output


def test_cli_refresh_prices_failure(runner, tmp_path):
    result = runner.invoke(
        app,
        [
            "refresh-prices",
            "--url",
            (tmp_path / "missing.json").as_uri(),
            "--cache-path",
            str(tmp_path / "c.json"),
        ],
    )
    assert result.exit_code != 0
    assert "Error" in result.output
