import json
import math
from datetime import datetime, timedelta
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from .core import TraceCollector, TraceSpan
from .decorator import _get_default_collector
from .format import fmt_cost, fmt_duration, fmt_number, fmt_throughput

app = typer.Typer(help="TraceForge - Trazabilidad para pipelines multi-agente")
console = Console()


@app.callback()
def _main(
    collector: Optional[str] = typer.Option(
        None,
        "--collector",
        "-c",
        help="Backend: memory, sqlite, postgres, clickhouse",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db-path",
        help="Ruta SQLite o DSN alternativo",
    ),
    dsn: Optional[str] = typer.Option(
        None,
        "--dsn",
        help="DSN para postgres o clickhouse",
    ),
):
    if not (collector or db_path or dsn):
        return
    from . import configure

    if dsn and not collector:
        collector = "clickhouse" if dsn.startswith(("http://", "https://")) else "postgres"
    configure(collector=collector or "sqlite", db_path=db_path, dsn=dsn)


def _span_dict(span: TraceSpan) -> dict:
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_id": span.parent_id,
        "agent": span.agent,
        "model": span.model,
        "status": span.status,
        "duration_ms": span.duration_ms,
        "tokens_input": span.tokens_input,
        "tokens_output": span.tokens_output,
        "cost_usd": span.cost_usd,
        "error": span.error,
        "input_truncated": span.input_truncated,
        "output_truncated": span.output_truncated,
        "started_at": span.started_at.isoformat() if span.started_at else None,
        "stream": span.stream,
        "ttft_ms": span.ttft_ms,
        "stream_chunks": span.stream_chunks,
        "metadata": span.metadata or {},
    }


def _percentile(sorted_values: list[int], p: float) -> int:
    if not sorted_values:
        return 0
    rank = math.ceil(p * len(sorted_values)) - 1
    return sorted_values[max(0, min(rank, len(sorted_values) - 1))]


def _build_span_tree(span: TraceSpan, collector: TraceCollector) -> Tree:
    label_parts = [f"[bold]{span.agent}[/bold]"]
    if span.model:
        label_parts.append(f"({span.model})")
    label_parts.append(f"→ {fmt_duration(span.duration_ms)}")
    if span.tokens_input or span.tokens_output:
        label_parts.append(f"| {fmt_number(span.tokens_input + span.tokens_output)} tokens")
    if span.stream:
        metrics = []
        if span.ttft_ms is not None:
            metrics.append(f"⏱ ttft {fmt_duration(span.ttft_ms)}")
        if span.throughput_tps:
            metrics.append(f"{fmt_throughput(span.throughput_tps)}")
        label_parts.append(f"| [cyan]{' · '.join(metrics)}[/cyan]")
    if span.cost_usd:
        label_parts.append(f"| {fmt_cost(span.cost_usd)}")
    if span.input_truncated or span.output_truncated:
        label_parts.append("[yellow]⚠ truncated[/yellow]")
    if span.status == "error":
        label_parts.append("[red]✗ ERROR[/red]")
    else:
        label_parts.append("[green]✓[/green]")

    tree = Tree(" ".join(label_parts))

    if span.input:
        inp = str(span.input)
        if len(inp) > 80:
            inp = inp[:80] + "..."
        tree.add(f"[dim]input:[/dim] {inp}")

    if span.error:
        tree.add(f"[red]error: {span.error}[/red]")

    for child_id in span.children:
        child = collector.get_span(child_id)
        if child:
            child_tree = _build_span_tree(child, collector)
            tree.add(child_tree)

    return tree


def show_trace(
    trace_id: str,
    tree: bool = True,
    collector: Optional[TraceCollector] = None,
) -> None:
    _collector = collector or _get_default_collector()
    spans = _collector.get_trace(trace_id)
    if not spans:
        console.print(f"[red]Trace {trace_id} not found[/red]")
        raise typer.Exit(code=1)

    roots = [s for s in spans if s.parent_id is None]
    total_duration = sum(s.duration_ms for s in spans)
    total_tokens = sum(s.tokens_input + s.tokens_output for s in spans)
    total_cost = sum(s.cost_usd for s in spans)
    error_count = sum(1 for s in spans if s.status == "error")

    console.print(f"\n[bold]Trace:[/bold] {trace_id}")
    summary = f"  Spans: {len(spans)} | Duration: {fmt_duration(total_duration)}"
    summary += f" | Tokens: {fmt_number(total_tokens)} | Cost (est.): {fmt_cost(total_cost)} | Errors: {error_count}"
    console.print(summary)

    for root in roots:
        span_tree = _build_span_tree(root, _collector)
        console.print(span_tree)


@app.command(name="list")
def list_traces(
    last: int = typer.Option(10, "--last", "-n", help="Número de trazas a mostrar"),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON"),
):
    _collector = _get_default_collector()
    trace_ids = _collector.list_traces(limit=last)

    if not trace_ids:
        console.print("[]" if json_output else "[yellow]No traces found[/yellow]")
        raise typer.Exit()

    rows = []
    for tid in reversed(trace_ids):
        spans = _collector.get_trace(tid)
        started = min((s.started_at for s in spans if s.started_at), default=None)
        rows.append(
            {
                "trace_id": tid,
                "spans": len(spans),
                "duration_ms": sum(s.duration_ms for s in spans),
                "tokens": sum(s.tokens_input + s.tokens_output for s in spans),
                "cost_usd": round(sum(s.cost_usd for s in spans), 6),
                "errors": sum(1 for s in spans if s.status == "error"),
                "started_at": started.isoformat() if started else "",
            }
        )

    if json_output:
        console.print(json.dumps(rows, indent=2, default=str))
        return

    table = Table(box=box.SIMPLE)
    table.add_column("Trace ID")
    table.add_column("Spans")
    table.add_column("Duration")
    table.add_column("Tokens")
    table.add_column("Cost (est.)")
    table.add_column("Errors")
    table.add_column("Started")

    for row in rows:
        started = row["started_at"]
        table.add_row(
            row["trace_id"][:8] + "...",
            str(row["spans"]),
            fmt_duration(row["duration_ms"]),
            fmt_number(row["tokens"]),
            fmt_cost(row["cost_usd"]),
            f"[red]{row['errors']}[/red]" if row["errors"] else "0",
            started[:19].replace("T", " ") if started else "-",
        )

    console.print(table)


@app.command()
def show(
    trace_id: str,
    tree: bool = typer.Option(True, "--tree", help="Mostrar como árbol"),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON"),
):
    if json_output:
        _collector = _get_default_collector()
        spans = _collector.get_trace(trace_id)
        if not spans:
            console.print(f"[red]Trace {trace_id} not found[/red]")
            raise typer.Exit(code=1)
        console.print(json.dumps([_span_dict(s) for s in spans], indent=2, default=str))
        return
    show_trace(trace_id, tree=tree)


@app.command()
def stats(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filtrar por agente"),
    since_days: Optional[int] = typer.Option(None, "--since", "-s", help="Días hacia atrás"),
    sort_by: str = typer.Option("cost", "--sort", help="Ordenar por: cost, tokens, spans, duration, errors"),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON"),
):
    _collector = _get_default_collector()
    since: Optional[datetime] = None
    if since_days is not None:
        since = datetime.now() - timedelta(days=since_days)

    spans = _collector.query(agent=agent, since=since)

    if not spans:
        console.print("{}" if json_output else "[yellow]No spans match the criteria[/yellow]")
        raise typer.Exit()

    by_agent: dict[str, dict] = {}
    for s in spans:
        if s.agent not in by_agent:
            by_agent[s.agent] = {"spans": 0, "tokens": 0, "cost": 0.0, "errors": 0, "durations": []}
        by_agent[s.agent]["spans"] += 1
        by_agent[s.agent]["tokens"] += s.tokens_input + s.tokens_output
        by_agent[s.agent]["cost"] += s.cost_usd
        by_agent[s.agent]["errors"] += 1 if s.status == "error" else 0
        by_agent[s.agent]["durations"].append(s.duration_ms)

    rows = []
    for agt, data in by_agent.items():
        durations = sorted(data["durations"])
        p95 = _percentile(durations, 0.95)
        avg = sum(durations) / len(durations) if durations else 0
        rows.append(
            {
                "agent": agt,
                "spans": data["spans"],
                "tokens": data["tokens"],
                "cost_usd": round(data["cost"], 6),
                "errors": data["errors"],
                "avg_duration_ms": round(avg, 2),
                "p95_duration_ms": p95,
            }
        )

    sort_keys = {
        "cost": "cost_usd",
        "tokens": "tokens",
        "spans": "spans",
        "duration": "avg_duration_ms",
        "errors": "errors",
    }
    key = sort_keys.get(sort_by, "cost_usd")
    rows.sort(key=lambda r: r[key], reverse=True)

    total_cost = sum(r["cost_usd"] for r in rows)

    if json_output:
        for r in rows:
            r["cost_pct"] = round(100.0 * r["cost_usd"] / total_cost, 2) if total_cost else 0.0
        console.print(json.dumps(rows, indent=2, default=str))
        return

    table = Table(box=box.SIMPLE)
    table.add_column("Agent")
    table.add_column("Spans")
    table.add_column("Total Tokens")
    table.add_column("Total Cost (est.)")
    table.add_column("% Cost")
    table.add_column("Errors")
    table.add_column("Avg Duration")
    table.add_column("P95 Duration")

    for row in rows:
        pct = 100.0 * row["cost_usd"] / total_cost if total_cost else 0.0
        table.add_row(
            row["agent"],
            str(row["spans"]),
            fmt_number(row["tokens"]),
            fmt_cost(row["cost_usd"]),
            f"{pct:.1f}%",
            f"[red]{row['errors']}[/red]" if row["errors"] else "0",
            fmt_duration(row["avg_duration_ms"]),
            fmt_duration(row["p95_duration_ms"]),
        )

    console.print(table)


@app.command()
def report(
    trace_id: str,
    output: str = typer.Option("traceforge_report.html", "--output", "-o", help="Archivo de salida"),
    fmt: str = typer.Option("html", "--format", "-f", help="Formato: html, json, markdown"),
):
    try:
        from .reporting import generate_report
    except ImportError:
        console.print("[red]Report requires jinja2: pip install traceforge[/red]")
        raise typer.Exit(code=1)

    _collector = _get_default_collector()
    try:
        result = generate_report(trace_id, format=fmt, output=output, collector=_collector)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if output:
        console.print(f"[green]Report saved to {output}[/green]")
    else:
        console.print(result)


@app.command()
def export(
    fmt: str = typer.Option("otel", "--format", "-f", help="Formato de exportación"),
    since_days: Optional[int] = typer.Option(None, "--since", "-s", help="Exportar desde hace N días"),
    trace_id: Optional[str] = typer.Option(None, "--trace-id", "-t", help="Exportar una traza específica"),
):
    _collector = _get_default_collector()

    if fmt == "otel":
        try:
            from .collector.otel import OTELCollector
        except ImportError:
            console.print("[red]OTEL export requires: pip install traceforge[otel][/red]")
            raise typer.Exit(code=1)

        since: Optional[datetime] = None
        if since_days is not None:
            since = datetime.now() - timedelta(days=since_days)

        if trace_id:
            spans = _collector.get_trace(trace_id)
        else:
            spans = _collector.query(since=since)

        if not spans:
            console.print("[yellow]No spans to export[/yellow]")
            raise typer.Exit()

        otel_collector = OTELCollector()
        for s in spans:
            otel_collector.save(s)

        console.print(f"[green]Exported {len(spans)} spans to OpenTelemetry[/green]")

    elif fmt == "json":
        since = None
        if since_days is not None:
            since = datetime.now() - timedelta(days=since_days)

        if trace_id:
            spans = _collector.get_trace(trace_id)
        else:
            spans = _collector.query(since=since)

        if not spans:
            console.print("[yellow]No spans to export[/yellow]")
            raise typer.Exit()

        data = []
        for s in spans:
            data.append(
                {
                    "span_id": s.span_id,
                    "trace_id": s.trace_id,
                    "parent_id": s.parent_id,
                    "agent": s.agent,
                    "model": s.model,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "tokens_input": s.tokens_input,
                    "tokens_output": s.tokens_output,
                    "cost_usd": s.cost_usd,
                    "error": s.error,
                    "input_truncated": s.input_truncated,
                    "output_truncated": s.output_truncated,
                }
            )

        output = json.dumps(data, indent=2, default=str)
        console.print(output)
    else:
        console.print(f"[red]Unknown format: {fmt}[/red]")
        raise typer.Exit(code=1)


@app.command()
def query(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filtrar por agente"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filtrar por estado"),
    min_duration_ms: Optional[int] = typer.Option(
        None,
        "--min-duration",
        "-d",
        help="Duración mínima (ms)",
    ),
    since_days: Optional[int] = typer.Option(None, "--since", help="Días hacia atrás"),
    limit: int = typer.Option(50, "--limit", "-n", help="Máximo de spans a mostrar"),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON"),
):
    _collector = _get_default_collector()
    since: Optional[datetime] = None
    if since_days is not None:
        since = datetime.now() - timedelta(days=since_days)

    spans = _collector.query(
        agent=agent,
        status=status,
        min_duration_ms=min_duration_ms,
        since=since,
    )
    if limit > 0:
        spans = list(reversed(spans[-limit:]))

    if not spans:
        console.print("[]" if json_output else "[yellow]No spans match[/yellow]")
        raise typer.Exit()

    if json_output:
        console.print(json.dumps([_span_dict(s) for s in spans], indent=2, default=str))
        return

    table = Table(box=box.SIMPLE)
    table.add_column("Span ID")
    table.add_column("Agent")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Tokens")
    table.add_column("Cost")

    for s in spans:
        table.add_row(
            s.span_id[:8] + "...",
            s.agent,
            s.model or "-",
            "[red]error[/red]" if s.status == "error" else "[green]ok[/green]",
            fmt_duration(s.duration_ms),
            fmt_number(s.tokens_input + s.tokens_output),
            fmt_cost(s.cost_usd),
        )

    console.print(table)


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Host a escuchar"),
    port: int = typer.Option(8080, "--port", "-p", help="Puerto del dashboard"),
    open_browser: bool = typer.Option(False, "--open", help="Abrir navegador"),
):
    from .dashboard import run_dashboard

    _collector = _get_default_collector()
    run_dashboard(collector=_collector, host=host, port=port, open_browser=open_browser)


@app.command()
def refresh_prices(
    source: str = typer.Option("litellm", "--source", help="Fuente del catálogo (litellm)"),
    url: Optional[str] = typer.Option(None, "--url", help="URL de precios personalizada"),
    cache_path: Optional[str] = typer.Option(None, "--cache-path", help="Ruta de la caché"),
    timeout: int = typer.Option(15, "--timeout", help="Timeout de descarga (segundos)"),
):
    """Descargar el catálogo de precios más reciente y cachearlo localmente."""
    from .pricing import price_changes
    from .pricing import refresh_prices as _refresh

    try:
        n = _refresh(source=source, url=url, cache_path=cache_path, timeout=timeout)
    except Exception as e:
        console.print(f"[red]Error al descargar precios: {e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]Precios actualizados: {n} modelos cargados[/green]")
    for change in price_changes():
        static, cached = change["static"], change["cached"]
        console.print(
            f"[yellow]⚠ {change['model']}: ${static['input']:.5f}/${static['output']:.5f} "
            f"→ ${cached['input']:.5f}/${cached['output']:.5f} (por 1K tokens)[/yellow]"
        )


@app.command()
def clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar el borrado"),
):
    if not yes:
        console.print("[yellow]This will delete all traces. Use --yes to confirm[/yellow]")
        raise typer.Exit(code=1)
    _get_default_collector().clear()
    console.print("[green]All traces cleared[/green]")


if __name__ == "__main__":
    app()
