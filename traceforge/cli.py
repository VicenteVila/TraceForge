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

app = typer.Typer(help="TraceForge - Trazabilidad para pipelines multi-agente")
console = Console()


def _percentile(sorted_values: list[int], p: float) -> int:
    if not sorted_values:
        return 0
    rank = math.ceil(p * len(sorted_values)) - 1
    return sorted_values[max(0, min(rank, len(sorted_values) - 1))]


def _build_span_tree(span: TraceSpan, collector: TraceCollector) -> Tree:
    label_parts = [f"[bold]{span.agent}[/bold]"]
    if span.model:
        label_parts.append(f"({span.model})")
    label_parts.append(f"→ {span.duration_ms}ms")
    if span.tokens_input or span.tokens_output:
        label_parts.append(f"| {span.tokens_input + span.tokens_output} tokens")
    if span.stream:
        metrics = []
        if span.ttft_ms is not None:
            metrics.append(f"⏱ ttft {span.ttft_ms:.0f}ms")
        if span.throughput_tps:
            metrics.append(f"{span.throughput_tps:.0f} tok/s")
        label_parts.append(f"| [cyan]{' · '.join(metrics)}[/cyan]")
    if span.cost_usd:
        label_parts.append(f"| ${span.cost_usd:.4f}")
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
    summary = f"  Spans: {len(spans)} | Duration: {total_duration}ms"
    summary += f" | Tokens: {total_tokens} | Cost: ${total_cost:.4f} | Errors: {error_count}"
    console.print(summary)

    for root in roots:
        span_tree = _build_span_tree(root, _collector)
        console.print(span_tree)


@app.command(name="list")
def list_traces(
    last: int = typer.Option(10, "--last", "-n", help="Número de trazas a mostrar"),
):
    _collector = _get_default_collector()
    trace_ids = _collector.list_traces(limit=last)

    if not trace_ids:
        console.print("[yellow]No traces found[/yellow]")
        raise typer.Exit()

    table = Table(box=box.SIMPLE)
    table.add_column("Trace ID")
    table.add_column("Spans")
    table.add_column("Duration")
    table.add_column("Tokens")
    table.add_column("Cost")
    table.add_column("Errors")

    for tid in reversed(trace_ids):
        spans = _collector.get_trace(tid)
        total_duration = sum(s.duration_ms for s in spans)
        total_tokens = sum(s.tokens_input + s.tokens_output for s in spans)
        total_cost = sum(s.cost_usd for s in spans)
        error_count = sum(1 for s in spans if s.status == "error")
        table.add_row(
            tid[:8] + "...",
            str(len(spans)),
            f"{total_duration}ms",
            str(total_tokens),
            f"${total_cost:.4f}",
            f"[red]{error_count}[/red]" if error_count else "0",
        )

    console.print(table)


@app.command()
def show(
    trace_id: str,
    tree: bool = typer.Option(True, "--tree", help="Mostrar como árbol"),
):
    show_trace(trace_id, tree=tree)


@app.command()
def stats(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filtrar por agente"),
    since_days: Optional[int] = typer.Option(None, "--since", "-s", help="Días hacia atrás"),
):
    _collector = _get_default_collector()
    since: Optional[datetime] = None
    if since_days is not None:
        since = datetime.now() - timedelta(days=since_days)

    spans = _collector.query(agent=agent, since=since)

    if not spans:
        console.print("[yellow]No spans match the criteria[/yellow]")
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

    table = Table(box=box.SIMPLE)
    table.add_column("Agent")
    table.add_column("Spans")
    table.add_column("Total Tokens")
    table.add_column("Total Cost")
    table.add_column("Errors")
    table.add_column("Avg Duration")
    table.add_column("P95 Duration")

    for agt, data in sorted(by_agent.items()):
        durations = sorted(data["durations"])
        p95 = _percentile(durations, 0.95)
        avg = sum(durations) / len(durations) if durations else 0
        table.add_row(
            agt,
            str(data["spans"]),
            str(data["tokens"]),
            f"${data['cost']:.4f}",
            f"[red]{data['errors']}[/red]" if data["errors"] else "0",
            f"{avg:.0f}ms",
            f"{p95}ms",
        )

    console.print(table)


@app.command()
def report(
    trace_id: str,
    output: str = typer.Option("traceforge_report.html", "--output", "-o",
                               help="Archivo de salida"),
    fmt: str = typer.Option("html", "--format", "-f",
                            help="Formato: html, json, markdown"),
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
    since_days: Optional[int] = typer.Option(None, "--since", "-s",
                                              help="Exportar desde hace N días"),
    trace_id: Optional[str] = typer.Option(None, "--trace-id", "-t",
                                            help="Exportar una traza específica"),
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
            data.append({
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
            })

        output = json.dumps(data, indent=2, default=str)
        console.print(output)
    else:
        console.print(f"[red]Unknown format: {fmt}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
