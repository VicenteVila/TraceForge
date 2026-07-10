PRICING_TABLE: dict[str, dict[str, float]] = {
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


def calculate_cost(model: str | None, tokens_input: int = 0, tokens_output: int = 0) -> float:
    if not model or model not in PRICING_TABLE:
        return 0.0
    rates = PRICING_TABLE[model]
    return (tokens_input / 1000 * rates["input"]) + (tokens_output / 1000 * rates["output"])


def get_model_list() -> list[str]:
    return sorted(PRICING_TABLE.keys())
