import pytest

from traceforge.pricing import PRICING_TABLE, calculate_cost, get_model_list


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
