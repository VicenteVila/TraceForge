import time
from types import SimpleNamespace

import pytest

from traceforge.auto import instrument_anthropic, instrument_openai
from traceforge.collector.memory import MemoryCollector
from traceforge.collector.sqlite import SQLiteCollector


class _Usage:
    def __init__(self, prompt=0, completion=0, input=0, output=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.input_tokens = input
        self.output_tokens = output


def _delta_chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _usage_chunk(prompt=0, completion=0):
    return SimpleNamespace(choices=[], usage=_Usage(prompt=prompt, completion=completion))


class _SyncStream:
    def __init__(self, chunks, fail_on=None):
        self._chunks = list(chunks)
        self._fail_on = fail_on
        self.closed = False
        self.response = "fake-http-response"

    def __iter__(self):
        for i, chunk in enumerate(self._chunks):
            if self._fail_on is not None and i >= self._fail_on:
                raise RuntimeError("connection dropped")
            time.sleep(0.002)
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def close(self):
        self.closed = True


class _AsyncStream:
    def __init__(self, chunks, fail_on=None):
        self._chunks = list(chunks)
        self._fail_on = fail_on
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def aclose(self):
        self.closed = True

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for i, chunk in enumerate(self._chunks):
            if self._fail_on is not None and i >= self._fail_on:
                raise RuntimeError("connection dropped")
            yield chunk


class _SyncMessagesStream:
    def __init__(self, events):
        self._events = list(events)

    def __iter__(self):
        for event in self._events:
            yield event


def _openai_stream_client(chunks, fail_on=None):
    class _Completions:
        def create(self, *args, **kwargs):
            if kwargs.get("stream"):
                return _SyncStream(chunks, fail_on=fail_on)
            raise AssertionError("non-stream path not expected")

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _openai_async_stream_client(chunks, fail_on=None):
    class _Completions:
        async def create(self, *args, **kwargs):
            if kwargs.get("stream"):
                return _AsyncStream(chunks, fail_on=fail_on)
            raise AssertionError("non-stream path not expected")

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _openai_chunks():
    return [
        _delta_chunk("Hello "),
        _delta_chunk("world"),
        _usage_chunk(prompt=10, completion=5),
    ]


def test_sync_stream_records_ttft_tokens_and_output():
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks())
    assert instrument_openai(client=client, agent="streamer", collector=collector) is True

    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    received = [chunk for chunk in stream]

    assert len(received) == 3
    spans = collector.query(agent="streamer")
    assert len(spans) == 1
    sp = spans[0]
    assert sp.stream is True
    assert sp.ttft_ms is not None
    assert sp.output == "Hello world"
    assert sp.tokens_input == 10
    assert sp.tokens_output == 5
    assert sp.stream_chunks == 3
    assert len(sp.chunk_offsets_ms) == 3
    assert sp.status == "ok"
    assert sp.duration_ms >= 0
    assert sp.throughput_tps > 0


def test_sync_stream_captures_full_prompt():
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks())
    instrument_openai(client=client, agent="stream_prompt", collector=collector)

    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    stream = client.chat.completions.create(model="gpt-4o", messages=messages, stream=True)
    list(stream)

    spans = collector.query(agent="stream_prompt")
    assert spans[0].input == messages
    assert spans[0].output == "Hello world"


def test_sync_stream_with_context_manager():
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks())
    instrument_openai(client=client, agent="ctx", collector=collector)

    with client.chat.completions.create(model="gpt-4o", stream=True) as stream:
        for chunk in stream:
            pass

    spans = collector.query(agent="ctx")
    assert len(spans) == 1
    assert spans[0].output == "Hello world"


def test_sync_stream_proxies_attributes():
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks())
    instrument_openai(client=client, agent="prox", collector=collector)

    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    assert stream.response == "fake-http-response"


def test_sync_stream_error_marks_span():
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks(), fail_on=1)
    instrument_openai(client=client, agent="boom", collector=collector)

    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    with pytest.raises(RuntimeError, match="connection dropped"):
        for chunk in stream:
            pass

    spans = collector.query(agent="boom")
    assert len(spans) == 1
    assert spans[0].status == "error"
    assert "RuntimeError" in spans[0].error
    assert spans[0].stream is True


def test_sync_stream_estimates_tokens_without_usage():
    collector = MemoryCollector()
    client = _openai_stream_client([_delta_chunk("one"), _delta_chunk(" two")])
    instrument_openai(client=client, agent="est", collector=collector)

    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    list(stream)

    spans = collector.query(agent="est")
    assert spans[0].tokens_output > 0
    assert spans[0].tokens_input == 0


@pytest.mark.asyncio
async def test_async_stream_records_metrics():
    collector = MemoryCollector()
    client = _openai_async_stream_client(_openai_chunks())
    assert instrument_openai(client=client, agent="async_stream", collector=collector) is True

    stream = await client.chat.completions.create(model="gpt-4o", stream=True)
    received = []
    async for chunk in stream:
        received.append(chunk)

    assert len(received) == 3
    spans = collector.query(agent="async_stream")
    assert len(spans) == 1
    sp = spans[0]
    assert sp.stream is True
    assert sp.output == "Hello world"
    assert sp.tokens_output == 5
    assert sp.stream_chunks == 3


@pytest.mark.asyncio
async def test_async_stream_error_marks_span():
    collector = MemoryCollector()
    client = _openai_async_stream_client(_openai_chunks(), fail_on=1)
    instrument_openai(client=client, agent="aboom", collector=collector)

    stream = await client.chat.completions.create(model="gpt-4o", stream=True)
    with pytest.raises(RuntimeError, match="connection dropped"):
        async for chunk in stream:
            pass

    spans = collector.query(agent="aboom")
    assert spans[0].status == "error"


def test_anthropic_streaming_events():
    collector = MemoryCollector()

    def msg_event(delta_text=None, usage=None):
        payload = {}
        if delta_text is not None:
            payload["delta"] = SimpleNamespace(text=delta_text)
        if usage is not None:
            payload["usage"] = usage
        return SimpleNamespace(**payload)

    events = [
        msg_event(usage=_Usage(input=7)),
        msg_event(delta_text="Hola"),
        msg_event(delta_text=" mundo"),
        msg_event(delta_text=None, usage=_Usage(output=3)),
    ]

    class _Messages:
        def create(self, *args, **kwargs):
            if kwargs.get("stream"):
                return _SyncMessagesStream(events)
            raise AssertionError("non-stream path not expected")

    client = SimpleNamespace(messages=_Messages())
    assert instrument_anthropic(client=client, agent="claude_stream", collector=collector) is True

    stream = client.messages.create(model="claude-3-5-sonnet", stream=True)
    list(stream)

    spans = collector.query(agent="claude_stream")
    assert len(spans) == 1
    sp = spans[0]
    assert sp.stream is True
    assert sp.output == "Hola mundo"
    assert sp.tokens_input == 7
    assert sp.tokens_output == 3


def test_streaming_fields_roundtrip_through_sqlite(tmp_path):
    collector = MemoryCollector()
    client = _openai_stream_client(_openai_chunks())
    instrument_openai(client=client, agent="persist", collector=collector)

    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    list(stream)

    source = collector.query(agent="persist")[0]
    sqlite = SQLiteCollector(str(tmp_path / "traces.db"))
    sqlite.save(source)
    restored = sqlite.get_span(source.span_id)

    assert restored.stream is True
    assert restored.ttft_ms == source.ttft_ms
    assert restored.stream_chunks == source.stream_chunks
    assert restored.chunk_offsets_ms == source.chunk_offsets_ms
    assert restored.output == "Hello world"
    assert restored.tokens_output == 5
