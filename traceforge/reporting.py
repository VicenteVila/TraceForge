from typing import Any, Optional

from .core import TraceCollector, TraceSpan
from .decorator import _get_default_collector

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TraceForge - Reporte {{ trace_id[:8] }}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
h1 { font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #f1f5f9; }
h2 { font-size: 18px; font-weight: 600; margin: 24px 0 12px; color: #94a3b8; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
.stat-card { background: #1e293b; border-radius: 8px; padding: 16px 20px; min-width: 120px; }
.stat-card .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 20px; font-weight: 600; margin-top: 4px; }
.chart-container { background: #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; color: #64748b; font-weight: 500; border-bottom: 1px solid #334155; }
td { padding: 8px 12px; border-bottom: 1px solid #1e293b; }
tr:hover td { background: #1e293b; }
.status-ok { color: #22c55e; }
.status-error { color: #ef4444; }
.agent-tag { display: inline-block; background: #334155; border-radius: 4px; padding: 2px 8px; font-size: 11px; color: #94a3b8; }
.truncated-tag { display: inline-block; background: #78350f; border-radius: 4px; padding: 2px 8px; font-size: 11px; color: #fbbf24; }
.stream-tag { margin-top: 4px; font-size: 11px; color: #22d3ee; }
</style>
</head>
<body>
<h1>TraceForge Report</h1>
<p style="color: #64748b; font-size: 14px; margin-bottom: 20px;">
  Trace: <code>{{ trace_id }}</code>
</p>

<div class="stats">
  <div class="stat-card"><div class="label">Spans</div><div class="value">{{ total_spans }}</div></div>
  <div class="stat-card"><div class="label">Duration</div><div class="value">{{ total_duration }}ms</div></div>
  <div class="stat-card"><div class="label">Tokens</div><div class="value">{{ total_tokens }}</div></div>
  <div class="stat-card"><div class="label">Cost</div><div class="value">${{ "%.4f"|format(total_cost) }}</div></div>
  <div class="stat-card"><div class="label">Errors</div><div class="value" style="color: {{ '#ef4444' if error_count > 0 else '#22c55e' }}">{{ error_count }}</div></div>
</div>

{% if gantt_json %}
<div class="chart-container">
  <h2>Gantt Chart</h2>
  <div id="gantt-chart"></div>
</div>
{% endif %}

{% if sankey_json %}
<div class="chart-container">
  <h2>Agent Flow</h2>
  <div id="sankey-chart"></div>
</div>
{% endif %}

<div class="chart-container">
  <h2>Span Details</h2>
  <table>
    <thead>
      <tr>
        <th>Depth</th>
        <th>Agent</th>
        <th>Model</th>
        <th>Status</th>
        <th>Duration</th>
        <th>Tokens</th>
        <th>Cost</th>
        <th>Data</th>
        <th>Error</th>
      </tr>
    </thead>
    <tbody>
    {% for span in spans %}
      <tr>
        <td>{{ span.depth }}</td>
        <td><span class="agent-tag">{{ span.agent }}</span></td>
        <td>{{ span.model or '-' }}</td>
        <td class="status-{{ span.status }}">{{ '✓' if span.status == 'ok' else '✗' }}</td>
        <td>{{ span.duration_ms }}ms</td>
        <td>
          {{ span.tokens_input + span.tokens_output }}
          {% if span.stream %}
            <div class="stream-tag">⏱ TTFT {{ "%.0f"|format(span.ttft_ms) }}ms · {{ span.throughput_tps }} tok/s · {{ span.stream_chunks }} chunks</div>
          {% endif %}
        </td>
        <td>${{ "%.4f"|format(span.cost_usd) }}</td>
        <td>
          {% if span.input_truncated or span.output_truncated %}
            <span class="truncated-tag">⚠ {{ 'input' if span.input_truncated }}{{ ', output' if span.output_truncated }} truncated</span>
          {% else %} - {% endif %}
        </td>
        <td style="color: #ef4444; max-width: 300px; overflow: hidden; text-overflow: ellipsis;">{{ span.error or '' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<script>
{% if gantt_json %}
var ganttData = {{ gantt_json | safe }};
Plotly.newPlot('gantt-chart', ganttData.data, ganttData.layout, {responsive: true});
{% endif %}
{% if sankey_json %}
var sankeyData = {{ sankey_json | safe }};
Plotly.newPlot('sankey-chart', sankeyData.data, sankeyData.layout, {responsive: true});
{% endif %}
</script>
</body>
</html>"""


def _build_span_tree_data(
    span: TraceSpan,
    spans_by_id: dict[str, TraceSpan],
    depth: int = 0,
) -> list[dict[str, Any]]:
    result = [
        {
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_id": span.parent_id or "",
            "agent": span.agent,
            "model": span.model or "",
            "status": span.status,
            "duration_ms": span.duration_ms,
            "tokens_input": span.tokens_input,
            "tokens_output": span.tokens_output,
            "cost_usd": span.cost_usd,
            "error": span.error or "",
            "input_truncated": span.input_truncated,
            "output_truncated": span.output_truncated,
            "stream": span.stream,
            "ttft_ms": span.ttft_ms,
            "stream_chunks": span.stream_chunks,
            "chunk_offsets_ms": span.chunk_offsets_ms,
            "throughput_tps": round(span.throughput_tps, 1) if span.throughput_tps else 0,
            "started_at": span.started_at,
            "finished_at": span.finished_at or span.started_at,
            "depth": depth,
        }
    ]
    for child_id in span.children:
        child = spans_by_id.get(child_id)
        if child:
            result.extend(_build_span_tree_data(child, spans_by_id, depth + 1))
    return result


def _build_gantt(
    spans: list[dict[str, Any]],
    spans_by_id: dict[str, TraceSpan],
) -> Optional[dict[str, Any]]:
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    labels = []
    starts = []
    ends = []
    colors = []
    hover_texts = []

    agent_colors = {
        "orchestrator": "#6366f1",
        "scoping": "#22c55e",
        "planner": "#f59e0b",
        "developer": "#3b82f6",
        "debugger": "#ec4899",
    }
    fallback_colors = ["#8b5cf6", "#14b8a6", "#f97316", "#06b6d4", "#a855f7", "#84cc16", "#64748b"]
    used_colors: dict[str, str] = {}
    color_idx = 0

    root = next((s for s in spans if not s["parent_id"]), None)
    if not root:
        return None

    def add_spans(span_data: dict[str, Any]) -> None:
        nonlocal color_idx
        agent = span_data["agent"]
        if agent not in used_colors:
            used_colors[agent] = agent_colors.get(agent, fallback_colors[color_idx % len(fallback_colors)])
            color_idx += 1

        raw_span = spans_by_id[span_data["span_id"]]
        label = f"{span_data['agent']}"
        if span_data["model"]:
            label += f" ({span_data['model']})"

        start_ms = (raw_span.started_at - root["started_at"]).total_seconds() * 1000
        dur = span_data["duration_ms"]

        labels.append(label)
        starts.append(start_ms)
        ends.append(start_ms + dur)
        colors.append(used_colors[agent])
        hover_texts.append(
            f"Agent: {span_data['agent']}<br>"
            f"Model: {span_data['model'] or '-'}<br>"
            f"Duration: {dur}ms<br>"
            f"Tokens: {span_data['tokens_input'] + span_data['tokens_output']}<br>"
            f"Cost: ${span_data['cost_usd']:.4f}<br>"
            f"Status: {span_data['status']}"
        )

    for s in spans:
        if s["parent_id"]:
            continue
        add_spans(s)
        _add_children(s, spans, add_spans)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[e - s for s, e in zip(starts, ends)],
            y=labels,
            orientation="h",
            marker=dict(color=colors),
            base=starts,
            customdata=list(zip(hover_texts)),
            hovertemplate="%{customdata[0]}<br>Start: %{base:.0f}ms<br>End: %{x:.0f}ms<extra></extra>",
        )
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=11),
        margin=dict(l=0, r=0, t=0, b=0),
        height=max(300, len(labels) * 30),
        xaxis=dict(title="Time (ms)", gridcolor="#334155"),
        yaxis=dict(
            title=None,
            autorange="reversed",
            gridcolor="#334155",
            tickfont=dict(size=10),
        ),
        bargap=0.3,
        showlegend=False,
    )

    return {"data": [fig.data[0].to_plotly_json()], "layout": fig.layout.to_plotly_json()}


def _add_children(parent: dict[str, Any], all_spans: list[dict[str, Any]], fn) -> None:
    for s in all_spans:
        if s["parent_id"] == parent["span_id"]:
            fn(s)
            _add_children(s, all_spans, fn)


def _build_sankey(spans: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    edges: list[tuple[str, str, str]] = []
    node_set: set[str] = set()

    for s in spans:
        node_set.add(s["agent"])
        if s["parent_id"]:
            parent_span = next((p for p in spans if p["span_id"] == s["parent_id"]), None)
            if parent_span:
                edges.append((parent_span["agent"], s["agent"], s["status"]))
                node_set.add(parent_span["agent"])

    if not edges:
        return None

    node_list = sorted(node_set)
    node_map = {n: i for i, n in enumerate(node_list)}

    source = []
    target = []
    colors_link = []

    for src, tgt, status in edges:
        source.append(node_map[src])
        target.append(node_map[tgt])
        colors_link.append("rgba(34, 197, 94, 0.3)" if status == "ok" else "rgba(239, 68, 68, 0.3)")

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    pad=15,
                    thickness=20,
                    line=dict(color="#334155", width=0.5),
                    label=node_list,
                    color="#6366f1",
                ),
                link=dict(
                    source=source,
                    target=target,
                    value=[1] * len(source),
                    color=colors_link,
                ),
            )
        ]
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=12),
        margin=dict(l=0, r=0, t=0, b=0),
        height=max(200, len(node_list) * 40),
    )

    return {"data": [fig.data[0].to_plotly_json()], "layout": fig.layout.to_plotly_json()}


def generate_report(
    trace_id: str,
    format: str = "html",
    output: Optional[str] = None,
    collector: Optional[TraceCollector] = None,
) -> str:
    _collector = collector or _get_default_collector()
    raw_spans = _collector.get_trace(trace_id)
    if not raw_spans:
        raise ValueError(f"Trace {trace_id} not found")

    spans_by_id = {s.span_id: s for s in raw_spans}

    for s in raw_spans:
        if s.parent_id and s.parent_id in spans_by_id and s.span_id not in spans_by_id[s.parent_id].children:
            spans_by_id[s.parent_id].children.append(s.span_id)

    roots = [s for s in raw_spans if s.parent_id is None]
    flat: list[dict[str, Any]] = []
    for root in roots:
        flat.extend(_build_span_tree_data(root, spans_by_id))

    total_spans = len(flat)
    total_duration = sum(s["duration_ms"] for s in flat)
    total_tokens = sum(s["tokens_input"] + s["tokens_output"] for s in flat)
    total_cost = sum(s["cost_usd"] for s in flat)
    error_count = sum(1 for s in flat if s["status"] == "error")

    if format == "json":
        import json

        result = json.dumps(
            {
                "trace_id": trace_id,
                "total_spans": total_spans,
                "total_duration_ms": total_duration,
                "total_tokens": total_tokens,
                "total_cost": total_cost,
                "error_count": error_count,
                "spans": flat,
            },
            indent=2,
            default=str,
        )
        if output:
            with open(output, "w") as f:
                f.write(result)
        return result

    if format == "markdown":
        lines = [
            f"# TraceForge Report: `{trace_id[:8]}...`",
            "",
            f"- **Spans:** {total_spans}",
            f"- **Duration:** {total_duration}ms",
            f"- **Tokens:** {total_tokens}",
            f"- **Cost:** ${total_cost:.4f}",
            f"- **Errors:** {error_count}",
            "",
            "| Agent | Model | Status | Duration | Tokens | Cost | Stream |",
            "|-------|-------|--------|----------|--------|------|--------|",
        ]
        for s in flat:
            status_char = "✓" if s["status"] == "ok" else "✗"
            stream_note = "-"
            if s["stream"]:
                ttft = f"{s['ttft_ms']:.0f}ms" if s["ttft_ms"] is not None else "-"
                stream_note = f"⏱ TTFT {ttft} · {s['throughput_tps']} tok/s"
            lines.append(
                f"| {s['agent']} | {s['model'] or '-'} | {status_char} "
                f"| {s['duration_ms']}ms | {s['tokens_input'] + s['tokens_output']} "
                f"| ${s['cost_usd']:.4f} | {stream_note} |"
            )
        result = "\n".join(lines)
        if output:
            with open(output, "w") as f:
                f.write(result)
        return result

    try:
        from jinja2 import Template
    except ImportError:
        raise ImportError("HTML report requires jinja2: pip install traceforge")

    gantt_json = _build_gantt(flat, spans_by_id)
    sankey_json = _build_sankey(flat)

    template = Template(HTML_TEMPLATE)
    html = template.render(
        trace_id=trace_id,
        total_spans=total_spans,
        total_duration=total_duration,
        total_tokens=total_tokens,
        total_cost=total_cost,
        error_count=error_count,
        spans=flat,
        gantt_json=gantt_json,
        sankey_json=sankey_json,
    )

    if output:
        with open(output, "w") as f:
            f.write(html)

    return html
