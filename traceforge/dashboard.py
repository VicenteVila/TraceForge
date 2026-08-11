"""Lightweight web dashboard (zero dependencies).

A small stdlib HTTP server exposing a JSON REST API over a ``TraceCollector``
plus a single-file SPA (no build step). Start it from Python or the CLI:

    python -m traceforge.dashboard --port 8080
    traceforge dashboard --port 8080

Endpoints:
    GET /api/health            {"ok": true, "traces": n}
    GET /api/traces?limit=20   recent trace summaries
    GET /api/trace/<id>        full spans of one trace
    GET /api/stats             per-agent metrics
    GET /api/query?agent=&status=&min_duration=&since_days=  filtered spans
"""

import json
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from .core import TraceCollector, TraceSpan
from .decorator import _get_default_collector


def _span_dict(span: TraceSpan) -> dict:
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_id": span.parent_id,
        "agent": span.agent,
        "model": span.model,
        "input": span.input,
        "output": span.output,
        "error": span.error,
        "status": span.status,
        "duration_ms": span.duration_ms,
        "tokens_input": span.tokens_input,
        "tokens_output": span.tokens_output,
        "cost_usd": span.cost_usd,
        "started_at": span.started_at.isoformat() if span.started_at else None,
        "stream": span.stream,
        "ttft_ms": span.ttft_ms,
        "stream_chunks": span.stream_chunks,
    }


def _trace_summary(collector: TraceCollector, trace_id: str) -> dict:
    spans = collector.get_trace(trace_id)
    return {
        "trace_id": trace_id,
        "spans": len(spans),
        "duration_ms": sum(s.duration_ms for s in spans),
        "tokens": sum(s.tokens_input + s.tokens_output for s in spans),
        "cost_usd": round(sum(s.cost_usd for s in spans), 6),
        "errors": sum(1 for s in spans if s.status == "error"),
        "started_at": min((s.started_at for s in spans if s.started_at), default=None).isoformat()
        if any(s.started_at for s in spans)
        else None,
    }


def _build_timeseries(collector: TraceCollector, bucket: str = "day") -> list[dict]:
    """Serie temporal de spans y coste, agrupada por hora o día."""
    from collections import defaultdict

    spans = collector.query()
    if bucket not in ("hour", "day"):
        bucket = "day"
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"spans": 0, "tokens": 0, "cost_usd": 0.0, "errors": 0})
    for s in spans:
        if not s.started_at:
            continue
        key = s.started_at.strftime("%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d")
        g = groups[key]
        g["spans"] += 1
        g["tokens"] += s.tokens_input + s.tokens_output
        g["cost_usd"] += s.cost_usd
        g["errors"] += 1 if s.status == "error" else 0
    return [
        {"bucket": k, **{kk: (round(v, 8) if kk == "cost_usd" else v) for kk, v in sorted(g.items())}}
        for k, g in sorted(groups.items())
    ]


class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def log_message(self, fmt, *args):  # quiet by default
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        params = self._params()
        if path == "/":
            return self._send_html(_HTML_PAGE)
        if path == "/api/health":
            return self._send_json({"ok": True, "traces": len(self.server.collector.list_traces(limit=1000))})
        if path == "/api/traces":
            limit = int(params.get("limit", 20))
            offset = int(params.get("offset", 0))
            traces = self.server.collector.list_traces(limit=limit, offset=offset)
            return self._send_json([_trace_summary(self.server.collector, t) for t in reversed(traces)])
        if path == "/api/trace-count":
            return self._send_json({"total": len(self.server.collector.list_traces(limit=100000))})
        if path == "/api/timeseries":
            return self._send_json(_build_timeseries(self.server.collector, params.get("bucket", "day")))
        if path == "/api/export.csv":
            return self._send_csv(self._export_rows(params))
        if path.startswith("/api/trace/"):
            trace_id = path[len("/api/trace/") :]
            spans = self.server.collector.get_trace(trace_id)
            if not spans:
                return self._send_json({"error": "trace not found"}, status=404)
            return self._send_json({"trace_id": trace_id, "spans": [_span_dict(s) for s in spans]})
        if path == "/api/stats":
            return self._send_json(self._stats())
        if path == "/api/query":
            return self._send_json([_span_dict(s) for s in self._query(params)])
        return self._send_json({"error": "not found"}, status=404)

    def _params(self) -> dict[str, str]:
        from urllib.parse import parse_qs, urlsplit

        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _query(self, params: dict[str, str]) -> list[TraceSpan]:
        since: Optional[datetime] = None
        if params.get("since_days"):
            since = datetime.now() - timedelta(days=int(params["since_days"]))
        return self.server.collector.query(
            agent=params.get("agent") or None,
            status=params.get("status") or None,
            min_duration_ms=int(params["min_duration"]) if params.get("min_duration") else None,
            since=since,
        )

    def _stats(self) -> list[dict]:
        spans = self.server.collector.query()
        by_agent: dict[str, dict[str, Any]] = {}
        for s in spans:
            if s.agent not in by_agent:
                by_agent[s.agent] = {"spans": 0, "tokens": 0, "cost": 0.0, "errors": 0, "durations": []}
            by_agent[s.agent]["spans"] += 1
            by_agent[s.agent]["tokens"] += s.tokens_input + s.tokens_output
            by_agent[s.agent]["cost"] += s.cost_usd
            by_agent[s.agent]["errors"] += 1 if s.status == "error" else 0
            by_agent[s.agent]["durations"].append(s.duration_ms)
        rows = []
        for agent, data in sorted(by_agent.items()):
            durations = sorted(data["durations"])
            avg = sum(durations) / len(durations) if durations else 0
            p95 = durations[max(0, min(int(0.95 * len(durations)) - 1, len(durations) - 1))] if durations else 0
            rows.append(
                {
                    "agent": agent,
                    "spans": data["spans"],
                    "tokens": data["tokens"],
                    "cost_usd": round(data["cost"], 6),
                    "errors": data["errors"],
                    "avg_duration_ms": round(avg, 2),
                    "p95_duration_ms": p95,
                }
            )
        return rows

    def _send_json(self, payload: Any, status: int = 200):
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _export_rows(self, params: dict[str, str]) -> list[dict]:
        spans = self._query(params)
        rows = []
        for s in spans:
            rows.append(
                {
                    "trace_id": s.trace_id,
                    "span_id": s.span_id,
                    "agent": s.agent,
                    "model": s.model or "",
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "tokens_input": s.tokens_input,
                    "tokens_output": s.tokens_output,
                    "cost_usd": round(s.cost_usd, 8),
                    "error": s.error or "",
                    "started_at": s.started_at.isoformat() if s.started_at else "",
                }
            )
        return rows

    def _send_csv(self, rows: list[dict], status: int = 200):
        import csv
        import io

        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            buf.write(
                "trace_id,span_id,agent,model,status,duration_ms,tokens_input,tokens_output,cost_usd,error,started_at\n"
            )
        body = buf.getvalue().encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="traceforge_export.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, collector: TraceCollector, address=("127.0.0.1", 8080)):
        self.collector = collector
        super().__init__(address, DashboardHandler)


def run_dashboard(
    collector: Optional[TraceCollector] = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    poll_interval: float = 0.5,
) -> None:
    """Serve the dashboard (blocking). Ctrl+C stops it."""
    _collector = collector or _get_default_collector()
    server = DashboardServer(_collector, (host, port))
    url = f"http://{host}:{port}/"
    print(f"TraceForge dashboard on {url}  (press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


_HTML_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>TraceForge</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#0f1117; color:#e6e6e6; }
  header { display:flex; gap:16px; align-items:center; padding:14px 22px; background:#171a23; border-bottom:1px solid #262b3a; position:sticky; top:0; z-index:10; }
  header h1 { font-size:16px; margin:0; color:#7dd3fc; }
  .tabs button { background:none; border:1px solid transparent; color:#9aa3b2; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  .tabs button.active { color:#7dd3fc; border-color:#2b3a52; background:#1a2332; }
  main { padding:22px; max-width: 1100px; margin: 0 auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #22273a; }
  th { color:#8b93a7; font-weight:600; }
  tr.clickable { cursor:pointer; }
  tr.clickable:hover { background:#171a23; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; }
  .ok { background:#14331f; color:#6ee7a0; }
  .err { background:#3a1518; color:#ff8f9d; }
  .right { text-align:right; }
  .muted { color:#6b7280; }
  .tree { font-size:13px; line-height:1.7; }
  .tree ul { list-style:none; padding-left:20px; border-left:1px dashed #2b3350; }
  .tree li { padding:2px 0; }
  .detail { background:#141823; border:1px solid #232a3e; border-radius:8px; padding:16px; margin-top:14px; }
  pre { white-space:pre-wrap; word-break:break-word; background:#0b0e14; padding:10px; border-radius:6px; font-size:12px; max-height:220px; overflow:auto; }
  input, select { background:#0b0e14; border:1px solid #2b3350; color:#e6e6e6; padding:6px 8px; border-radius:6px; }
  .toolbar { display:flex; gap:10px; margin-bottom:14px; align-items:center; flex-wrap:wrap; }
  a { color:#7dd3fc; text-decoration:none; }
  .bars { margin-top:10px; }
  .bar-row { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
  .bar-label { width:130px; font-size:12px; color:#9aa3b2; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .bar-track { flex:1; background:#1a2030; border-radius:4px; height:14px; overflow:hidden; }
  .bar-fill { height:100%; background:linear-gradient(90deg,#22d3ee,#a855f7); border-radius:4px; }
  .bar-val { width:90px; text-align:right; font-size:12px; }
  .pager { display:flex; gap:10px; align-items:center; margin-top:10px; font-size:13px; }
  .pager button { background:#1a2332; border:1px solid #2b3350; color:#e6e6e6; border-radius:6px; padding:4px 12px; cursor:pointer; }
  .pager button:disabled { opacity:.4; cursor:default; }
  .chart-box { background:#141823; border:1px solid #232a3e; border-radius:8px; padding:14px; margin-top:16px; }
</style>
</head>
<body>
<header>
  <h1>TraceForge</h1>
  <div class="tabs">
    <button id="tab-traces" class="active" onclick="show('traces')">Traces</button>
    <button id="tab-stats" onclick="show('stats')">Stats</button>
    <button id="tab-query" onclick="show('query')">Query</button>
  </div>
  <label class="muted" style="margin-left:auto"><input type="checkbox" id="autorefresh" checked onchange="tick()"> auto</label>
</header>
<main>
  <p class="muted" style="font-size:12px; margin-top:0;">Coste estimado (precio público de mercado) · free tier: $0</p>
  <div id="view-traces">
    <div id="traces"></div>
    <div id="pager"></div>
    <div id="trace-detail"></div>
  </div>
  <div id="view-stats" style="display:none">
    <div id="stats"></div>
    <div id="stat-charts"></div>
  </div>
  <div id="view-query" style="display:none">
    <div class="toolbar">
      <input id="q-agent" placeholder="agent">
      <select id="q-status"><option value="">any status</option><option>ok</option><option>error</option></select>
      <input id="q-min" type="number" placeholder="min duration (ms)">
      <input id="q-since" type="number" placeholder="since days">
      <button onclick="runQuery()">Search</button>
      <button onclick="exportQuery()">Export CSV</button>
    </div>
    <div id="query-results"></div>
  </div>
</main>
<script>
let detailShown = null;
let pageOffset = 0;
const PAGE_SIZE = 20;
function $(id){ return document.getElementById(id); }
function show(name){
  ["traces","stats","query"].forEach(v => $("view-"+v).style.display = v===name ? "" : "none");
  ["traces","stats","query"].forEach(v => $("tab-"+v).classList.toggle("active", v===name));
  if (name === "traces") { $("traces").innerHTML = ""; $("trace-detail").innerHTML = ""; pageOffset = 0; detailShown = null; loadTraces(); }
  if (name === "stats") loadStats();
}
function esc(s){ const d=document.createElement("div"); d.textContent = s==null?"":String(s); return d.innerHTML; }
function money(v){ return "$"+ (v||0).toFixed(4); }
function fmtDur(ms){ ms=Number(ms)||0; if(ms<0||isNaN(ms)) return ms+"ms"; if(ms<1000) return ms+"ms"; if(ms<60000){ const s=ms/1000; return (s<10? s.toFixed(1):s.toFixed(0))+"s"; } const m=Math.floor(ms/60000), s=Math.round((ms%60000)/1000); return m+"m "+(s>=1?s+"s":""); }
function fmtNum(n){ return (Number(n)||0).toLocaleString("en-US"); }
async function get(path){
  const r = await fetch(path);
  return r.json();
}
async function loadTraces(){
  const data = await get("/api/traces?limit=" + PAGE_SIZE + "&offset=" + pageOffset);
  const t = $("traces");
  const total = (await get("/api/trace-count")).total || 0;
  if (!data.length){ t.innerHTML = '<p class="muted">No traces yet.</p>'; $("pager").innerHTML=""; return; }
  t.innerHTML = "<table><thead><tr><th>Trace</th><th class='right'>Spans</th><th class='right'>Duration</th><th class='right'>Tokens</th><th class='right'>Cost</th><th class='right'>Errors</th><th>Started</th></tr></thead><tbody>" +
    data.map(r => `<tr class="clickable" onclick="openTrace('${esc(r.trace_id)}')">
      <td>${esc(r.trace_id.slice(0,8))}…</td><td class="right">${r.spans}</td><td class="right">${fmtDur(r.duration_ms)}</td>
      <td class="right">${fmtNum(r.tokens)}</td><td class="right">${money(r.cost_usd)}</td>
      <td class="right">${r.errors ? '<span class="badge err">'+r.errors+'</span>' : "0"}</td>
      <td class="muted">${r.started_at ? r.started_at.slice(0,19).replace("T"," ") : ""}</td></tr>`).join("") +
    "</tbody></table>";
  const from = pageOffset + 1, to = pageOffset + data.length;
  $("pager").innerHTML = '<div class="pager">' +
    '<button onclick="pagePrev()" ' + (pageOffset===0?'disabled':'') + '>‹ Prev</button>' +
    '<span class="muted">' + from + '–' + to + ' de ' + total + '</span>' +
    '<button onclick="pageNext()" ' + (to>=total?'disabled':'') + '>Next ›</button>' +
    '</div>';
}
async function pagePrev(){ pageOffset = Math.max(0, pageOffset - PAGE_SIZE); loadTraces(); }
async function pageNext(){ pageOffset += PAGE_SIZE; loadTraces(); }
async function openTrace(id){
  const data = await get("/api/trace/" + id);
  $("trace-detail").innerHTML = renderTree(id, data.spans) + renderSpans(data.spans);
  $("trace-detail").scrollIntoView({behavior:"smooth"});
}
function renderTree(id, spans){
  const byParent = {};
  spans.forEach(s => { (byParent[s.parent_id || "__root__"] = byParent[s.parent_id || "__root__"] || []).push(s); });
  function node(sid){
    const s = spans.find(x => x.span_id === sid);
    const kids = (byParent[sid] || []).map(k => node(k.span_id)).join("");
    const badge = s.status === "error" ? '<span class="badge err">ERROR</span>' : '<span class="badge ok">ok</span>';
    return `<li>${badge} <b>${esc(s.agent)}</b> ${esc(s.model||"")} → ${fmtDur(s.duration_ms)} · ${fmtNum(s.tokens_input+s.tokens_output)} tok · ${money(s.cost_usd)}${kids ? "<ul>"+kids+"</ul>" : ""}</li>`;
  }
  const roots = (byParent["__root__"] || []).map(r => node(r.span_id)).join("");
  return `<h3>Trace ${esc(id.slice(0,8))}…</h3><div class="tree"><ul>${roots}</ul></div>`;
}
function renderSpans(spans){
  return spans.map(s => `<div class="detail">
    <b>${esc(s.agent)}</b> ${esc(s.model||"")} <span class="muted">(${esc(s.span_id.slice(0,8))}…)</span> ·
    <span class="${s.status==="error"?"err":"ok"}">${esc(s.status)}</span> · ${fmtDur(s.duration_ms)} · ${fmtNum(s.tokens_input+s.tokens_output)} tok · ${money(s.cost_usd)}${s.ttft_ms!=null ? " · ttft "+fmtDur(s.ttft_ms) : ""}
    ${s.error ? "<p class='err'>"+esc(s.error)+"</p>" : ""}
    <details><summary>input</summary><pre>${esc(JSON.stringify(s.input))}</pre></details>
    <details><summary>output</summary><pre>${esc(JSON.stringify(s.output))}</pre></details>
  </div>`).join("");
}
async function loadStats(){
  const data = await get("/api/stats");
  const t = $("stats");
  if (!data.length){ t.innerHTML = '<p class="muted">No data.</p>'; return; }
  t.innerHTML = "<table><thead><tr><th>Agent</th><th class='right'>Spans</th><th class='right'>Tokens</th><th class='right'>Cost</th><th class='right'>Errors</th><th class='right'>Avg ms</th><th class='right'>P95 ms</th></tr></thead><tbody>" +
    data.map(r => `<tr><td>${esc(r.agent)}</td><td class="right">${r.spans}</td><td class="right">${fmtNum(r.tokens)}</td><td class="right">${money(r.cost_usd)}</td><td class="right">${r.errors}</td><td class="right">${fmtDur(r.avg_duration_ms)}</td><td class="right">${fmtDur(r.p95_duration_ms)}</td></tr>`).join("") +
    "</tbody></table>";
  renderCostBars(data);
  loadCharts(data);
}
function renderCostBars(data){
  const maxCost = Math.max(...data.map(r => r.cost_usd), 0.0001);
  const html = "<h3>Cost by agent (est.)</h3><div class='bars'>" +
    data.map(r => {
      const pct = (r.cost_usd / maxCost * 100).toFixed(1);
      return `<div class="bar-row"><span class="bar-label">${esc(r.agent)}</span><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="bar-val">${money(r.cost_usd)}</span></div>`;
    }).join("") + "</div>";
  $("stats").insertAdjacentHTML("beforeend", html);
}
async function loadCharts(statsData){
  const box = $("stat-charts");
  if (typeof Plotly === "undefined"){ box.innerHTML = '<p class="muted">Gráficos requieren conexión a Plotly CDN.</p>'; return; }
  box.innerHTML = '<div class="chart-box"><h3>Cost by agent</h3><div id="chart-cost"></div></div>' +
                  '<div class="chart-box"><h3>Spans over time</h3><div id="chart-ts"></div></div>';
  const cost = await get("/api/stats");
  Plotly.newPlot("chart-cost", [{
    x: cost.map(r => r.cost_usd), y: cost.map(r => r.agent), type: "bar", orientation: "h",
    marker: {color: "#22d3ee"}, text: cost.map(r => money(r.cost_usd)), textposition: "auto"
  }], {paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)", font:{color:"#e6e6e6"}, margin:{l:120,r:30,t:10,b:30}}, {responsive:true});
  const ts = await get("/api/timeseries?bucket=day");
  if (ts.length){
    Plotly.newPlot("chart-ts", [{
      x: ts.map(r => r.bucket), y: ts.map(r => r.spans), type: "scatter", mode: "lines+markers",
      name: "spans", line: {color: "#a855f7"}
    }, {
      x: ts.map(r => r.bucket), y: ts.map(r => r.cost_usd), type: "scatter", mode: "lines+markers",
      name: "cost (est.)", yaxis: "y2", line: {color: "#22d3ee"}
    }], {paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)", font:{color:"#e6e6e6"}, margin:{l:60,r:60,t:10,b:40}, yaxis:{title:"spans"}, yaxis2:{title:"$", overlaying:"y", side:"right"}}, {responsive:true});
  }
}
async function runQuery(){
  const p = new URLSearchParams();
  if ($("q-agent").value) p.set("agent", $("q-agent").value);
  if ($("q-status").value) p.set("status", $("q-status").value);
  if ($("q-min").value) p.set("min_duration", $("q-min").value);
  if ($("q-since").value) p.set("since_days", $("q-since").value);
  const data = await get("/api/query?" + p.toString());
  const t = $("query-results");
  if (!data.length){ t.innerHTML = '<p class="muted">No matches.</p>'; return; }
  t.innerHTML = "<table><thead><tr><th>Span</th><th>Agent</th><th>Model</th><th>Status</th><th class='right'>Duration</th><th class='right'>Cost</th></tr></thead><tbody>" +
    data.map(s => `<tr><td>${esc(s.span_id.slice(0,8))}…</td><td>${esc(s.agent)}</td><td>${esc(s.model||"-")}</td><td><span class="badge ${s.status==="error"?"err":"ok"}">${esc(s.status)}</span></td><td class="right">${fmtDur(s.duration_ms)}</td><td class="right">${money(s.cost_usd)}</td></tr>`).join("") +
    "</tbody></table>";
}
function exportQuery(){
  const p = new URLSearchParams();
  if ($("q-agent").value) p.set("agent", $("q-agent").value);
  if ($("q-status").value) p.set("status", $("q-status").value);
  if ($("q-min").value) p.set("min_duration", $("q-min").value);
  if ($("q-since").value) p.set("since_days", $("q-since").value);
  window.location.href = "/api/export.csv?" + p.toString();
}
function tick(){
  if ($("autorefresh").checked){ clearInterval(window.__ti); window.__ti = setInterval(loadTraces, 5000); }
  else { clearInterval(window.__ti); }
}
loadTraces();
tick();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TraceForge web dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--open", action="store_true", help="abrir navegador")
    args = parser.parse_args()
    run_dashboard(host=args.host, port=args.port, open_browser=args.open)
