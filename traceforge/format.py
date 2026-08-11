"""Formateo inteligente de métricas para reportes, dashboard y CLI.

Helpers puros (sin I/O) para mostrar duraciones, costes, tokens y
rendimiento de forma legible sin perder precisión en valores pequeños.
"""

from __future__ import annotations

from typing import Union

Number = Union[int, float]


def fmt_number(value: Number) -> str:
    """Número con separador de miles: 12500 -> '12,500'."""
    try:
        return f"{value:,}"
    except (TypeError, ValueError):
        return f"{value}"


def fmt_tokens(value: Number) -> str:
    """Tokens con separador de miles."""
    return fmt_number(value)


def fmt_duration(ms: Number) -> str:
    """Duración legible: 850 -> '850ms', 2500 -> '2.5s', 130000 -> '2m 10s'."""
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return f"{ms}"
    if ms < 0 or ms != ms:  # negativo o NaN
        return f"{ms:g}ms"
    if ms < 1000:
        return f"{ms:g}ms"
    if ms < 60_000:
        s = ms / 1000
        return f"{s:.1f}s" if s < 10 else f"{s:.0f}s"
    total_s = ms / 1000
    m = int(total_s // 60)
    s = total_s % 60
    if m < 60:
        return f"{m}m {s:.0f}s" if s >= 1 else f"{m}m"
    h = int(m // 60)
    m = m % 60
    return f"{h}h {m}m" if m else f"{h}h"


def fmt_cost(usd: Number) -> str:
    """Coste legible: <0.01 -> 4 decimales, <1 -> 3, >=1 -> 2 + miles."""
    try:
        usd = float(usd)
    except (TypeError, ValueError):
        return f"${usd}"
    if usd < 0 or usd != usd:
        return f"${usd:g}"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:,.2f}"


def fmt_throughput(tps: Number) -> str:
    """Tokens/segundo legible: 1200 -> '1.2k tok/s'."""
    try:
        tps = float(tps)
    except (TypeError, ValueError):
        return f"{tps}"
    if tps >= 1000:
        return f"{tps / 1000:.1f}k tok/s"
    return f"{tps:g} tok/s"
