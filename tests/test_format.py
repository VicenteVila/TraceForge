"""Tests de traceforge.format (formateo inteligente de métricas)."""

import pytest

from traceforge.format import (
    fmt_cost,
    fmt_duration,
    fmt_number,
    fmt_throughput,
    fmt_tokens,
)


def test_fmt_number_thousands():
    assert fmt_number(12500) == "12,500"
    assert fmt_number(0) == "0"
    assert fmt_number(999) == "999"


def test_fmt_tokens():
    assert fmt_tokens(12500) == "12,500"


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "0ms"),
        (850, "850ms"),
        (2500, "2.5s"),
        (8500, "8.5s"),
        (15000, "15s"),
        (130000, "2m 10s"),
        (60000, "1m"),
        (7300000, "2h 1m"),
        (3600000, "1h"),
    ],
)
def test_fmt_duration_cases(ms, expected):
    assert fmt_duration(ms) == expected


def test_fmt_duration_edge():
    assert fmt_duration(-5) == "-5ms"
    assert fmt_duration(float("nan")) == "nanms"


@pytest.mark.parametrize(
    ("usd", "expected"),
    [
        (0.0, "$0.0000"),
        (0.0009, "$0.0009"),
        (0.0123, "$0.012"),
        (0.999, "$0.999"),
        (1.234, "$1.23"),
        (1234.56, "$1,234.56"),
    ],
)
def test_fmt_cost_cases(usd, expected):
    assert fmt_cost(usd) == expected


def test_fmt_cost_edge():
    assert fmt_cost(-0.5) == "$-0.5"
    assert fmt_cost(float("nan")) == "$nan"


def test_fmt_throughput():
    assert fmt_throughput(1200) == "1.2k tok/s"
    assert fmt_throughput(900) == "900 tok/s"
    assert fmt_throughput(0) == "0 tok/s"
