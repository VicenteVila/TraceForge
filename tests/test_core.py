import warnings

import pytest

from traceforge.core import (
    MAX_INPUT_LEN,
    MAX_LIST_ITEMS,
    MAX_OUTPUT_LEN,
    _capture_input,
    _capture_output,
    set_truncation_limits,
)


def _reset_limits():
    set_truncation_limits(
        max_input_len=MAX_INPUT_LEN,
        max_output_len=MAX_OUTPUT_LEN,
        max_list_items=MAX_LIST_ITEMS,
    )


def test_capture_input_flags_truncation():
    big = "x" * 5000
    with pytest.warns(RuntimeWarning, match="truncated captured input"):
        value, truncated = _capture_input((big,), {"k": "short"})
    assert truncated is True
    assert len(value["args"][0]) < len(big)
    assert "truncated" in value["args"][0]


def test_capture_input_short_does_not_truncate():
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        value, truncated = _capture_input(("short",), {"k": "v"})
    assert truncated is False
    assert not recorded
    assert value["args"][0] == "short"


def test_capture_output_flags_truncation():
    big = "y" * 6000
    with pytest.warns(RuntimeWarning, match="truncated captured output"):
        value, truncated = _capture_output(big)
    assert truncated is True
    assert len(value) < len(big)


def test_list_truncation_flagged():
    with pytest.warns(RuntimeWarning, match="truncated captured input"):
        value, truncated = _capture_input((list(range(50)),), {})
    assert truncated is True
    assert len(value["args"][0]) <= MAX_LIST_ITEMS + 1
    assert "(50)" in value["args"][0][-1]


def test_limits_are_configurable():
    set_truncation_limits(max_input_len=10, max_output_len=20, max_list_items=3)
    try:
        with pytest.warns(RuntimeWarning):
            value, truncated = _capture_input(("abcdefghijklmnop",), {})
        assert truncated is True
        assert value["args"][0].startswith("abcdefghij")
        assert "truncated" in value["args"][0]
    finally:
        _reset_limits()


def test_truncation_can_be_disabled():
    set_truncation_limits(max_input_len=0, max_output_len=0, max_list_items=0)
    try:
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            value, truncated = _capture_output("z" * 100_000)
        assert truncated is False
        assert not recorded
        assert value == "z" * 100_000
    finally:
        _reset_limits()


def test_negative_limits_rejected():
    with pytest.raises(ValueError):
        set_truncation_limits(max_input_len=-1)
