import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from traceforge import trace
from traceforge.collector.memory import MemoryCollector
from traceforge.dashboard import DashboardServer


@pytest.fixture
def collector():
    c = MemoryCollector()

    @trace(agent="dash_root", model="gemini-2.5-flash", collector=c)
    def root():
        return child()

    @trace(agent="dash_child", model="gemini-2.5-flash", collector=c)
    def child():
        return "done"

    root()
    return c


@pytest.fixture
def server(collector):
    srv = DashboardServer(collector, ("127.0.0.1", 0))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def base(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


def _get(url):
    try:
        with urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except HTTPError as e:
        return e.code, e.read().decode()


def test_health(base):
    status, body = _get(base + "/api/health")
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True
    assert data["traces"] == 1


def test_index_serves_spa(base):
    status, body = _get(base + "/")
    assert status == 200
    assert "TraceForge" in body
    assert "/api/traces" in body


def test_traces_list(base):
    status, body = _get(base + "/api/traces?limit=5")
    assert status == 200
    data = json.loads(body)
    assert len(data) == 1
    assert data[0]["spans"] == 2
    assert data[0]["errors"] == 0


def test_trace_detail(base, collector):
    tid = collector.get_last_trace_id()
    status, body = _get(base + f"/api/trace/{tid}")
    assert status == 200
    data = json.loads(body)
    assert len(data["spans"]) == 2
    assert {s["agent"] for s in data["spans"]} == {"dash_root", "dash_child"}


def test_trace_detail_not_found(base):
    status, body = _get(base + "/api/trace/nope")
    assert status == 404
    assert "not found" in json.loads(body)["error"]


def test_stats(base):
    status, body = _get(base + "/api/stats")
    assert status == 200
    data = json.loads(body)
    assert {row["agent"] for row in data} == {"dash_root", "dash_child"}


def test_query_filter(base):
    status, body = _get(base + "/api/query?agent=dash_child")
    assert status == 200
    data = json.loads(body)
    assert len(data) == 1
    assert data[0]["agent"] == "dash_child"


def test_unknown_route_404(base):
    status, _ = _get(base + "/api/nope")
    assert status == 404
