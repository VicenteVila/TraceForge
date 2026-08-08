import json
import sys

import pytest

import traceforge
from traceforge.collector.memory import MemoryCollector


@pytest.fixture
def populated_module():
    traceforge.configure(collector="memory")

    @traceforge.trace(agent="api_agent")
    def run():
        return "done"

    run()
    trace_id = traceforge.get_last_trace_id()
    assert trace_id is not None
    return trace_id


def test_show_tree_format(populated_module, capsys):
    traceforge.show(populated_module, format="tree")
    output = capsys.readouterr().out
    assert "api_agent" in output


def test_show_json_format(populated_module, capsys):
    traceforge.show(populated_module, format="json")
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["total_spans"] == 1
    assert data["spans"][0]["agent"] == "api_agent"


def test_show_unsupported_format(populated_module):
    with pytest.raises(ValueError, match="Unsupported format"):
        traceforge.show(populated_module, format="markdown")


def test_configure_auto_trace_without_sdks_does_not_crash(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("openai") or name.startswith("anthropic"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    results = traceforge.instrument(collector=MemoryCollector())

    assert set(results.keys()) == {"openai", "anthropic"}
    assert results["openai"] is False
    assert results["anthropic"] is False


def test_configure_auto_trace_flag():
    import traceforge.auto as auto

    original = auto.instrument
    try:
        auto.instrument = lambda collector=None: {"openai": False, "anthropic": False}
        traceforge.configure(collector="memory", auto_trace=True)
    finally:
        auto.instrument = original

    assert traceforge.get_last_trace_id() is None


def test_init_returns_provider_results():
    import traceforge.auto as auto

    original = auto.instrument
    try:
        auto.instrument = lambda collector=None, providers=None: {
            name: False for name in (providers or ["openai", "anthropic"])
        }
        results = traceforge.init(auto_instrument=["openai", "langchain"])
    finally:
        auto.instrument = original

    assert set(results) == {"openai", "langchain"}


def test_init_without_instrumentation_configures_collector(tmp_path):
    db_path = tmp_path / "init.db"
    results = traceforge.init(collector="sqlite", db_path=str(db_path))
    assert results == {}

    @traceforge.trace(agent="via_init")
    def work():
        return "ok"

    work()
    assert traceforge.get_last_trace_id() is not None


def test_init_redact_pii_flag():
    from traceforge.redact import redact_value

    traceforge.init(redact_pii=False)
    value, hits = redact_value("a@b.com")
    assert hits == 0
    assert value == "a@b.com"

    traceforge.init(redact_pii=True)
    value, hits = redact_value("a@b.com")
    assert hits == 1
    assert value == "<email>"


def test_configure_postgres_without_driver_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError, match=r"traceforge\[postgres\]"):
        traceforge.configure(collector="postgres", dsn="postgresql://localhost/tf")


def test_configure_clickhouse_without_driver_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
    with pytest.raises(ImportError, match=r"traceforge\[clickhouse\]"):
        traceforge.configure(collector="clickhouse", dsn="http://localhost:8123/tf")
