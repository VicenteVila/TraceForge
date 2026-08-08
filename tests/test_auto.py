from types import SimpleNamespace

import pytest

from traceforge.auto import (
    TraceForgeLangChainHandler,
    instrument,
    instrument_anthropic,
    instrument_openai,
)
from traceforge.collector.memory import MemoryCollector


class _FakeUsage:
    def __init__(self, prompt=0, completion=0, input=0, output=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.input_tokens = input
        self.output_tokens = output


class _FakeOpenAIResponse:
    def __init__(self):
        self.usage = _FakeUsage(prompt=10, completion=5)


class _FakeAnthropicResponse:
    def __init__(self):
        self.usage = _FakeUsage(input=7, output=3)


def _openai_client():
    class _Completions:
        def create(self, *args, **kwargs):
            return _FakeOpenAIResponse()

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _openai_async_client():
    class _Completions:
        async def create(self, *args, **kwargs):
            return _FakeOpenAIResponse()

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _anthropic_client():
    class _Messages:
        def create(self, *args, **kwargs):
            return _FakeAnthropicResponse()

    return SimpleNamespace(messages=_Messages())


def _anthropic_async_client():
    class _Messages:
        async def create(self, *args, **kwargs):
            return _FakeAnthropicResponse()

    return SimpleNamespace(messages=_Messages())


def test_instrument_openai_sync():
    collector = MemoryCollector()
    client = _openai_client()
    assert instrument_openai(client=client, agent="openai_call", collector=collector) is True

    client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

    spans = collector.query(agent="openai_call")
    assert len(spans) == 1
    assert spans[0].model == "gpt-4o"
    assert spans[0].tokens_input == 10
    assert spans[0].tokens_output == 5
    assert spans[0].status == "ok"


@pytest.mark.asyncio
async def test_instrument_openai_async():
    collector = MemoryCollector()
    client = _openai_async_client()
    assert instrument_openai(client=client, agent="openai_call", collector=collector) is True

    await client.chat.completions.create(model="gpt-4o", messages=[])

    spans = collector.query(agent="openai_call")
    assert len(spans) == 1
    assert spans[0].tokens_input == 10


def test_instrument_openai_is_idempotent():
    client = _openai_client()
    collector = MemoryCollector()
    assert instrument_openai(client=client, collector=collector) is True
    assert instrument_openai(client=client, collector=collector) is False


def test_instrument_openai_error_traced():
    collector = MemoryCollector()

    class _Boom:
        def create(self, *args, **kwargs):
            raise RuntimeError("api down")

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Boom()))
    assert instrument_openai(client=client, agent="openai_call", collector=collector) is True

    with pytest.raises(RuntimeError, match="api down"):
        client.chat.completions.create(model="gpt-4o")

    spans = collector.query(agent="openai_call")
    assert spans[0].status == "error"
    assert "RuntimeError" in spans[0].error


def test_instrument_anthropic_sync():
    collector = MemoryCollector()
    client = _anthropic_client()
    assert instrument_anthropic(client=client, agent="claude_call", collector=collector) is True

    client.messages.create(model="claude-3-5-sonnet", messages=[])

    spans = collector.query(agent="claude_call")
    assert len(spans) == 1
    assert spans[0].tokens_input == 7
    assert spans[0].tokens_output == 3


@pytest.mark.asyncio
async def test_instrument_anthropic_async():
    collector = MemoryCollector()
    client = _anthropic_async_client()
    assert instrument_anthropic(client=client, agent="claude_call", collector=collector) is True

    await client.messages.create(model="claude-3-5-sonnet", messages=[])

    spans = collector.query(agent="claude_call")
    assert len(spans) == 1
    assert spans[0].tokens_input == 7


def test_instrument_reports_installed_sdks():
    results = instrument(collector=MemoryCollector())
    assert set(results.keys()) == {"openai", "anthropic"}
    assert isinstance(results["openai"], bool)
    assert isinstance(results["anthropic"], bool)


def test_langchain_handler_traces_llm():
    collector = MemoryCollector()
    handler = TraceForgeLangChainHandler(agent="llm", collector=collector)

    handler.on_llm_start({"name": "gpt-4o"}, ["hello"], run_id=1)

    response = SimpleNamespace(generations=[[SimpleNamespace(text="world")]])
    handler.on_llm_end(response, run_id=1)

    spans = collector.query(agent="llm")
    assert len(spans) == 1
    assert spans[0].model == "gpt-4o"
    assert spans[0].output == "world"
    assert spans[0].status == "ok"


def test_langchain_handler_traces_error_without_reraising():
    collector = MemoryCollector()
    handler = TraceForgeLangChainHandler(agent="llm", collector=collector)

    handler.on_llm_start({}, [], run_id=2)
    handler.on_llm_error(RuntimeError("boom"), run_id=2)

    spans = collector.query(agent="llm")
    assert len(spans) == 1
    assert spans[0].status == "error"
    assert "RuntimeError" in spans[0].error


def test_openai_wrapper_captures_full_prompt():
    collector = MemoryCollector()
    client = _openai_client()
    instrument_openai(client=client, agent="chat", collector=collector)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Tell me a joke."},
    ]
    client.chat.completions.create(model="gpt-4o", messages=messages)

    spans = collector.query(agent="chat")
    assert len(spans) == 1
    assert spans[0].input == messages
    assert spans[0].input[0]["content"] == "You are a helpful assistant."


def test_openai_wrapper_redacts_pii_in_prompt():
    collector = MemoryCollector()
    client = _openai_client()
    instrument_openai(client=client, agent="chat", collector=collector)

    client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "email me at alice@corp.io"}],
    )

    spans = collector.query(agent="chat")
    text = spans[0].input[0]["content"]
    assert "alice@corp.io" not in text
    assert "<email>" in text


def test_instrument_langchain_registers_global_handler(monkeypatch):
    import sys
    import types

    class _FakeContextManager:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    registered = []

    langchain_core = types.ModuleType("langchain_core")
    callbacks = types.ModuleType("langchain_core.callbacks")
    callbacks.set_handler = lambda handler: registered.append(handler) or _FakeContextManager()
    langchain_core.callbacks = callbacks
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks)

    from traceforge.auto import instrument

    collector = MemoryCollector()
    results = instrument(collector=collector, providers=["langchain"])

    assert results == {"langchain": True}
    assert len(registered) == 1
    assert registered[0].collector is collector


def test_instrument_unknown_provider_skipped():
    from traceforge.auto import instrument

    results = instrument(collector=MemoryCollector(), providers=["does-not-exist"])
    assert results == {}
