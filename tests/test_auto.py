from types import SimpleNamespace

import pytest

try:
    import httpx
except ImportError:  # pragma: no cover - only needed by the real-SDK tests
    httpx = None

from traceforge.auto import (
    TraceForgeLangChainHandler,
    instrument,
    instrument_anthropic,
    instrument_openai,
)
from traceforge.collector.memory import MemoryCollector

_SDK_PATCH_TARGETS = [
    ("openai", "openai.resources.chat.completions", "Completions", "create"),
    ("openai", "openai.resources.chat.completions", "AsyncCompletions", "create"),
    ("anthropic", "anthropic.resources.messages", "Messages", "create"),
    ("anthropic", "anthropic.resources.messages", "AsyncMessages", "create"),
]


@pytest.fixture(autouse=True)
def _restore_sdk_patches():
    """Restore real SDK methods after every test so global instrument() calls
    in one test cannot poison the class-level patches used by another."""
    originals = {}
    for provider, module_name, class_name, attr in _SDK_PATCH_TARGETS:
        try:
            module = __import__(module_name, fromlist=[class_name])
            target = getattr(module, class_name)
            originals[(module_name, class_name, attr)] = getattr(target, attr)
        except (ImportError, AttributeError):
            continue
    yield
    for (module_name, class_name, attr), original in originals.items():
        module = __import__(module_name, fromlist=[class_name])
        target = getattr(module, class_name)
        setattr(target, attr, original)


@pytest.fixture(scope="module", autouse=True)
def _warmup_real_sdks():
    """SDK imports and lazy response-typing are expensive on slow filesystems
    (e.g. WSL /mnt/c). Pre-warm once per module so the real-SDK tests run fast."""
    import importlib.util

    if importlib.util.find_spec("openai") is None and importlib.util.find_spec("anthropic") is None:
        return

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "warmup",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "w"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key="warmup", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "w"}])
    except ImportError:
        pass

    try:
        from anthropic import Anthropic

        def anthropic_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "msg_w",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-5-sonnet-20241022",
                    "content": [{"type": "text", "text": "w"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            )

        client = Anthropic(
            api_key="warmup", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(anthropic_handler))
        )
        client.messages.create(
            model="claude-3-5-sonnet-20241022", max_tokens=10, messages=[{"role": "user", "content": "w"}]
        )
    except ImportError:
        pass


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


# ── Validación con SDKs reales (httpx.MockTransport, sin red ni API keys) ──


def _openai_mock_response(request: "httpx.Request") -> "httpx.Response":
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def _openai_mock_stream(request: "httpx.Request") -> "httpx.Response":
    chunks = (
        'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
        '"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
        'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    return httpx.Response(200, content=chunks, headers={"content-type": "text/event-stream"})


def _anthropic_mock_response(request: "httpx.Request") -> "httpx.Response":
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    )


def test_real_openai_sync():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    from traceforge.auto import instrument_openai

    collector = MemoryCollector()
    client = openai.OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(_openai_mock_response)),
    )
    assert instrument_openai(client=client, agent="openai", collector=collector) is True

    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "Hello!"

    spans = collector.query(agent="openai")
    assert len(spans) == 1
    assert spans[0].model == "gpt-4o-mini"
    assert spans[0].tokens_input == 10
    assert spans[0].tokens_output == 5
    assert spans[0].input == [{"role": "user", "content": "hi"}]
    assert spans[0].status == "ok"


@pytest.mark.asyncio
async def test_real_openai_async():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    from traceforge.auto import instrument_openai

    collector = MemoryCollector()
    client = openai.AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_openai_mock_response)),
    )
    assert instrument_openai(client=client, agent="openai", collector=collector) is True

    resp = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "Hello!"

    spans = collector.query(agent="openai")
    assert len(spans) == 1
    assert spans[0].tokens_input == 10


def test_real_openai_streaming():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    from traceforge.auto import instrument_openai

    collector = MemoryCollector()
    client = openai.OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(_openai_mock_stream)),
    )
    assert instrument_openai(client=client, agent="openai", collector=collector) is True

    stream = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    text = "".join(ch.choices[0].delta.content or "" for ch in stream)

    spans = collector.query(agent="openai")
    assert len(spans) == 1
    assert text == "Hello"
    assert spans[0].stream is True
    assert spans[0].stream_chunks == 3
    assert spans[0].ttft_ms is not None
    assert spans[0].output == "Hello"


def test_real_anthropic_sync():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    from traceforge.auto import instrument_anthropic

    collector = MemoryCollector()
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(_anthropic_mock_response)),
    )
    assert instrument_anthropic(client=client, agent="claude", collector=collector) is True

    resp = client.messages.create(
        model="claude-3-5-sonnet-20241022", max_tokens=100, messages=[{"role": "user", "content": "hola"}]
    )
    assert resp.content[0].text == "Hello from Claude"

    spans = collector.query(agent="claude")
    assert len(spans) == 1
    assert spans[0].tokens_input == 7
    assert spans[0].tokens_output == 3
    assert spans[0].input == [{"role": "user", "content": "hola"}]


@pytest.mark.asyncio
async def test_real_anthropic_async():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    from traceforge.auto import instrument_anthropic

    collector = MemoryCollector()
    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_anthropic_mock_response)),
    )
    assert instrument_anthropic(client=client, agent="claude", collector=collector) is True

    resp = await client.messages.create(
        model="claude-3-5-sonnet-20241022", max_tokens=100, messages=[{"role": "user", "content": "hola"}]
    )
    assert resp.content[0].text == "Hello from Claude"

    spans = collector.query(agent="claude")
    assert len(spans) == 1
    assert spans[0].tokens_input == 7


def test_real_sdk_span_nests_under_orchestration():
    """Auto-instrumented call inside a root span must become a child of the
    orchestration trace (contextvar propagation through instrument())."""
    from traceforge.auto import instrument_openai
    from traceforge.context import span

    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")

    collector = MemoryCollector()
    client = openai.OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(_openai_mock_response)),
    )
    assert instrument_openai(client=client, agent="openai", collector=collector) is True

    with span(agent="pipeline", collector=collector):
        client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])

    spans = collector.get_trace(collector.get_last_trace_id())
    root = next(s for s in spans if s.parent_id is None)
    assert root.agent == "pipeline"
    llm_span = next(s for s in spans if s.agent == "openai")
    assert llm_span.parent_id == root.span_id
