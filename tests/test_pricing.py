import json
import warnings

import pytest

from traceforge import pricing
from traceforge.pricing import PRICING_TABLE, calculate_cost, get_model_list


def test_unknown_model_warns():
    with pytest.warns(RuntimeWarning, match="No pricing data"):
        cost = calculate_cost("nonexistent-model", tokens_input=1000, tokens_output=500)
    assert cost == 0.0


def test_none_model_does_not_warn():
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        calculate_cost(None, tokens_input=1000)
    assert not recorded


def test_known_model_calculation():
    cost = calculate_cost("gemini-2.5-flash", tokens_input=1000, tokens_output=500)
    expected = (1000 / 1000 * 0.00015) + (500 / 1000 * 0.00060)
    assert cost == pytest.approx(expected)


def test_unknown_model_returns_zero():
    cost = calculate_cost("nonexistent-model", tokens_input=1000, tokens_output=500)
    assert cost == 0.0


def test_none_model_returns_zero():
    cost = calculate_cost(None, tokens_input=1000, tokens_output=500)
    assert cost == 0.0


def test_zero_tokens():
    cost = calculate_cost("gpt-4o", tokens_input=0, tokens_output=0)
    assert cost == 0.0


def test_input_only_cost():
    cost = calculate_cost("gpt-4o-mini", tokens_input=2000, tokens_output=0)
    expected = 2000 / 1000 * 0.000150
    assert cost == pytest.approx(expected)


def test_get_model_list():
    models = get_model_list()
    assert "gemini-2.5-flash" in models
    assert "gpt-4o" in models
    assert "llama-3.3-70b" in models
    assert models == sorted(models)


def test_all_models_have_valid_rates():
    for model, rates in PRICING_TABLE.items():
        assert "input" in rates
        assert "output" in rates
        assert rates["input"] >= 0
        assert rates["output"] >= 0


@pytest.fixture
def pricing_state():
    saved_table = dict(pricing._dynamic_table)
    saved_path = pricing._cache_path
    yield
    pricing._dynamic_table = saved_table
    pricing._cache_path = saved_path


def test_refresh_prices_from_url_with_cache(pricing_state, tmp_path):
    src = tmp_path / "prices.json"
    src.write_text(json.dumps({
        "nova-model": {
            "input_cost_per_token": 0.000002,
            "output_cost_per_token": 0.000008,
        },
    }))
    cache = tmp_path / "cache.json"

    n = pricing.refresh_prices(url=src.as_uri(), cache_path=str(cache))

    assert n == 1
    assert cache.exists()
    cost = calculate_cost("nova-model", tokens_input=1000, tokens_output=1000)
    assert cost == pytest.approx(0.002 + 0.008)


def test_refresh_prices_persists_and_reloads(pricing_state, tmp_path):
    src = tmp_path / "prices.json"
    src.write_text(json.dumps({
        "cached-model": {
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
        },
    }))
    cache = tmp_path / "cache.json"
    pricing.refresh_prices(url=src.as_uri(), cache_path=str(cache))

    pricing._dynamic_table = {}
    pricing.set_pricing_cache_path(str(cache))
    assert "cached-model" in pricing._dynamic_table
    cost = calculate_cost("cached-model", tokens_input=1000, tokens_output=0)
    assert cost == pytest.approx(0.001)


def test_refresh_prices_skips_entries_without_rates(pricing_state, tmp_path):
    src = tmp_path / "prices.json"
    src.write_text(json.dumps({
        "has-rates": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002},
        "no-rates": {"context_window": 128000},
    }))
    cache = tmp_path / "cache.json"
    n = pricing.refresh_prices(url=src.as_uri(), cache_path=str(cache))
    assert n == 1
    assert "no-rates" not in pricing._dynamic_table
