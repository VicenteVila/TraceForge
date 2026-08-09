import json
import urllib.request
import warnings
from pathlib import Path
from typing import Optional

PRICING_TABLE: dict[str, dict[str, float]] = {
    "gpt-oss-120b": {"input": 0.00035, "output": 0.00075},
    "cerebras/gpt-oss-120b": {"input": 0.00035, "output": 0.00075},
    "gemma-4-31b": {"input": 0.00013, "output": 0.00040},
    "cerebras/gemma-4-31b": {"input": 0.00013, "output": 0.00040},
    "codestral-latest": {"input": 0.00030, "output": 0.00090},
    "mistral/codestral-latest": {"input": 0.00030, "output": 0.00090},
    "mistral-large-latest": {"input": 0.00050, "output": 0.00150},
    "mistral/mistral-large-latest": {"input": 0.00050, "output": 0.00150},
    "nvidia/llama-3.3-nemotron-super-49b-v1": {"input": 0.00013, "output": 0.00040},
    "llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    "groq/llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.000300},
    "gemini-1.5-flash-8b": {"input": 0.0000375, "output": 0.000150},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.00500},
    "gemini-2.0-flash": {"input": 0.00010, "output": 0.00040},
    "gemini-2.0-flash-lite": {"input": 0.000075, "output": 0.00030},
    "gemini-2.5-flash": {"input": 0.00015, "output": 0.00060},
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.01000},
    "gpt-4o": {"input": 0.00250, "output": 0.01000},
    "gpt-4o-mini": {"input": 0.000150, "output": 0.000600},
    "gpt-4-turbo": {"input": 0.01000, "output": 0.03000},
    "gpt-4": {"input": 0.03000, "output": 0.06000},
    "o1-mini": {"input": 0.00110, "output": 0.00440},
    "o1-preview": {"input": 0.01500, "output": 0.06000},
    "o3-mini": {"input": 0.00110, "output": 0.00440},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-3-sonnet": {"input": 0.00300, "output": 0.01500},
    "claude-3-opus": {"input": 0.01500, "output": 0.07500},
    "claude-3.5-sonnet": {"input": 0.00300, "output": 0.01500},
    "claude-3.5-haiku": {"input": 0.00080, "output": 0.00400},
    "claude-4-sonnet": {"input": 0.00300, "output": 0.01500},
    "llama-3.1-8b": {"input": 0.00005, "output": 0.00005},
    "llama-3.1-70b": {"input": 0.00059, "output": 0.00079},
    "llama-3.1-405b": {"input": 0.00270, "output": 0.00270},
    "llama-3.2-1b": {"input": 0.00003, "output": 0.00003},
    "llama-3.2-3b": {"input": 0.00005, "output": 0.00005},
    "llama-3.2-11b": {"input": 0.00008, "output": 0.00008},
    "llama-3.2-90b": {"input": 0.00070, "output": 0.00090},
    "llama-3.3-70b": {"input": 0.00059, "output": 0.00079},
    "llama-4-scout": {"input": 0.00015, "output": 0.00015},
    "llama-4-maverick": {"input": 0.00020, "output": 0.00020},
    "deepseek-v3": {"input": 0.00027, "output": 0.00110},
    "deepseek-r1": {"input": 0.00055, "output": 0.00219},
    "deepseek-r1-distill-llama-70b": {"input": 0.00055, "output": 0.00219},
    "deepseek-coder:6.7b": {"input": 0.00014, "output": 0.00028},
    "mistral-small": {"input": 0.00020, "output": 0.00060},
    "mistral-medium": {"input": 0.00270, "output": 0.00810},
    "mistral-large": {"input": 0.00400, "output": 0.01200},
    "codestral": {"input": 0.00025, "output": 0.00100},
    "groq-llama-3.3-70b": {"input": 0.00059, "output": 0.00079},
    "groq-llama-3.1-8b": {"input": 0.00005, "output": 0.00005},
    "groq-mixtral-8x7b": {"input": 0.00024, "output": 0.00024},
    "groq-gemma2-9b": {"input": 0.00020, "output": 0.00020},
    "qwen-2.5-72b": {"input": 0.00035, "output": 0.00120},
    "qwen-2.5-coder-32b": {"input": 0.00030, "output": 0.00090},
    "command-r": {"input": 0.00015, "output": 0.00060},
    "command-r-plus": {"input": 0.00060, "output": 0.00240},
}

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

_cache_path = Path.home() / ".cache" / "traceforge" / "pricing_cache.json"
_dynamic_table: dict[str, dict[str, float]] = {}


def _load_cache() -> None:
    global _dynamic_table
    try:
        if _cache_path.exists():
            _dynamic_table = json.loads(_cache_path.read_text())
    except Exception:
        _dynamic_table = {}


_load_cache()


def refresh_prices(
    source: str = "litellm",
    url: Optional[str] = None,
    cache_path: Optional[str] = None,
    timeout: int = 15,
) -> int:
    """Fetch the latest per-model pricing and cache it locally.

    The default ``source="litellm"`` reads LiteLLM's public price catalog
    (``input_cost_per_token`` / ``output_cost_per_token``, converted to per-1k).
    Pass a custom ``url`` (e.g. your own pricing API) that returns the same
    JSON shape. Returns the number of models loaded.
    """
    global _cache_path
    if cache_path is not None:
        _cache_path = Path(cache_path)
    fetch_url = url or LITELLM_URL
    with urllib.request.urlopen(fetch_url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())

    merged: dict[str, dict[str, float]] = {}
    for model, info in data.items():
        if not isinstance(info, dict):
            continue
        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue
        merged[model] = {
            "input": float(input_cost) * 1000,
            "output": float(output_cost) * 1000,
        }

    if not merged:
        warnings.warn("No pricing data found in the fetched source", RuntimeWarning)
        return 0

    global _dynamic_table
    _dynamic_table = merged
    try:
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        _cache_path.write_text(json.dumps(merged, indent=2))
    except OSError:
        pass
    return len(merged)


def set_pricing_cache_path(path: str) -> None:
    global _cache_path
    _cache_path = Path(path)
    _load_cache()


def calculate_cost(model: str | None, tokens_input: int = 0, tokens_output: int = 0) -> float:
    if not model:
        return 0.0
    rates = PRICING_TABLE.get(model) or _dynamic_table.get(model)
    if rates is None:
        warnings.warn(
            f"No pricing data for model {model!r}; cost set to 0.0",
            RuntimeWarning,
            stacklevel=2,
        )
        return 0.0
    return (tokens_input / 1000 * rates["input"]) + (tokens_output / 1000 * rates["output"])


def get_model_list() -> list[str]:
    return sorted(set(PRICING_TABLE) | set(_dynamic_table))


def price_changes() -> list[dict[str, object]]:
    """Compare cached (dynamic) prices against the static table.

    Returns a list of ``{model, static, cached}`` rows for every model that is
    present in both the static ``PRICING_TABLE`` and the LiteLLM cache, but with
    different rates. Useful to warn when a provider changed its market price.
    """
    changes: list[dict[str, object]] = []
    for model, static in PRICING_TABLE.items():
        cached = _dynamic_table.get(model)
        if cached is None:
            continue
        if cached.get("input") != static["input"] or cached.get("output") != static["output"]:
            changes.append(
                {
                    "model": model,
                    "static": static,
                    "cached": {"input": cached.get("input"), "output": cached.get("output")},
                }
            )
    return changes
