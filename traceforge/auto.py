"""Auto-instrumentation for popular LLM SDKs.

Call ``traceforge.instrument()`` (or ``traceforge.configure(auto_trace=True)``)
to enable automatic tracing of supported SDKs without decorating functions
manually.

Currently instruments:
- OpenAI / AsyncOpenAI chat completions (monkey-patched, sync + async)
- Anthropic / AsyncAnthropic messages (monkey-patched, sync + async)
- LangChain / LangGraph via a drop-in callback handler
- LlamaIndex via a drop-in callback handler

Streaming responses (``stream=True``) are traced too: the span stays open while
the stream is consumed and records the time-to-first-token (``ttft_ms``), per-chunk
arrival offsets (``chunk_offsets_ms``) and token throughput.
"""

import functools
import inspect
import re
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, Optional

from .context import span
from .core import TraceCollector, TraceSpan, _merged_span_metadata
from .decorator import (
    _current_parent_id,
    _current_trace_id,
    _get_default_collector,
    _on_new_trace,
    _on_trace_finished,
)

MAX_CHUNK_OFFSETS = 200


def _patch_method(target: Any, name: str, factory: Callable[[Callable], Callable]) -> bool:
    if not hasattr(target, name):
        return False
    original = getattr(target, name)
    if getattr(original, "_traceforge_patched", False):
        return False
    wrapper = factory(original)
    setattr(wrapper, "_traceforge_patched", True)
    try:
        setattr(target, name, wrapper)
    except (AttributeError, TypeError):
        return False
    return True


def _new_span(
    agent: str,
    model: Optional[str],
    collector: Optional[TraceCollector],
) -> TraceSpan:
    return TraceSpan(
        trace_id=_current_trace_id.get() or str(uuid.uuid4()),
        span_id=str(uuid.uuid4()),
        parent_id=_current_parent_id.get(),
        agent=agent,
        model=model,
        metadata=_merged_span_metadata(None),
        started_at=datetime.now(),
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(re.findall(r"\S+", text)))


def _openai_chunk_text(chunk: Any) -> Optional[str]:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(getattr(choices[0], "delta", None), "content", None)


def _anthropic_chunk_text(event: Any) -> Optional[str]:
    delta = getattr(event, "delta", None)
    return getattr(delta, "text", None) if delta is not None else None


def _extract_prompt(kwargs: dict) -> Any:
    for key in ("messages", "prompt"):
        if kwargs.get(key) is not None:
            return kwargs[key]
    return None


def _llm_wrapper_factory(
    agent: str,
    collector: Optional[TraceCollector],
    usage_extractor: Callable[[Any], tuple[int, int]],
    chunk_text: Callable[[Any], Optional[str]],
):
    def factory(original: Callable) -> Callable:
        # openai/anthropic wrap async methods with @required_args (functools.wraps),
        # so `iscoroutinefunction` on the raw attribute is False. unwrap first.
        if inspect.iscoroutinefunction(inspect.unwrap(original)):

            @functools.wraps(original)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                model = kwargs.get("model")
                _collector = collector or _get_default_collector()
                if not kwargs.get("stream"):
                    with span(agent=agent, model=model, collector=_collector) as sp:
                        prompt = _extract_prompt(kwargs)
                        if prompt is not None:
                            sp.set_input(prompt)
                        try:
                            response = await original(*args, **kwargs)
                        except Exception as e:
                            sp.set_error(f"{type(e).__name__}: {str(e)}")
                            raise
                        sp.set_output(response)
                        sp.set_tokens(*usage_extractor(response))
                        return response

                sp = _new_span(agent, model, _collector)
                is_new_trace = _current_trace_id.get() is None
                if is_new_trace:
                    _on_new_trace(sp.trace_id)
                prompt = _extract_prompt(kwargs)
                if prompt is not None:
                    sp.set_input(prompt)
                started = perf_counter()
                try:
                    stream = await original(*args, **kwargs)
                except Exception as e:
                    sp.set_error(f"{type(e).__name__}: {str(e)}")
                    sp.close()
                    _collector.save(sp)
                    if is_new_trace:
                        _on_trace_finished(sp.trace_id)
                    raise
                return _AsyncStreamProxy(stream, sp, _collector, started, chunk_text, usage_extractor, is_new_trace)

            return async_wrapper

        @functools.wraps(original)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            model = kwargs.get("model")
            _collector = collector or _get_default_collector()
            if not kwargs.get("stream"):
                with span(agent=agent, model=model, collector=_collector) as sp:
                    prompt = _extract_prompt(kwargs)
                    if prompt is not None:
                        sp.set_input(prompt)
                    try:
                        response = original(*args, **kwargs)
                    except Exception as e:
                        sp.set_error(f"{type(e).__name__}: {str(e)}")
                        raise
                    sp.set_output(response)
                    sp.set_tokens(*usage_extractor(response))
                    return response

            sp = _new_span(agent, model, _collector)
            is_new_trace = _current_trace_id.get() is None
            if is_new_trace:
                _on_new_trace(sp.trace_id)
            prompt = _extract_prompt(kwargs)
            if prompt is not None:
                sp.set_input(prompt)
            started = perf_counter()
            try:
                stream = original(*args, **kwargs)
            except Exception as e:
                sp.set_error(f"{type(e).__name__}: {str(e)}")
                sp.close()
                _collector.save(sp)
                if is_new_trace:
                    _on_trace_finished(sp.trace_id)
                raise
            return _SyncStreamProxy(stream, sp, _collector, started, chunk_text, usage_extractor, is_new_trace)

        return wrapper

    return factory


class _StreamMixin:
    def __init__(
        self,
        inner: Any,
        span: TraceSpan,
        collector: Optional[TraceCollector],
        started: float,
        chunk_text: Callable[[Any], Optional[str]],
        usage_extractor: Callable[[Any], tuple[int, int]],
        is_new_trace: bool = False,
    ):
        self._inner = inner
        self._span = span
        self._collector = collector
        self._started = started
        self._chunk_text = chunk_text
        self._usage_extractor = usage_extractor
        self._is_new_trace = is_new_trace
        self._finalized = False
        self._chunks = 0
        self._offsets: list[float] = []
        self._text_parts: list[str] = []
        self._tokens_in = 0
        self._tokens_out = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _on_chunk(self, chunk: Any) -> None:
        self._chunks += 1
        offset = (perf_counter() - self._started) * 1000
        if self._chunks == 1:
            self._span.ttft_ms = round(offset, 1)
        if len(self._offsets) < MAX_CHUNK_OFFSETS:
            self._offsets.append(round(offset, 1))
        text = self._chunk_text(chunk)
        if text:
            self._text_parts.append(text)
        tokens_in, tokens_out = self._usage_extractor(chunk)
        self._tokens_in = max(self._tokens_in, tokens_in)
        self._tokens_out = max(self._tokens_out, tokens_out)

    def _finalize(self, error: Optional[BaseException] = None) -> None:
        if self._finalized:
            return
        self._finalized = True
        sp = self._span
        text = "".join(self._text_parts)
        tokens_out = self._tokens_out or (_estimate_tokens(text) if text else 0)
        sp.stream = True
        sp.stream_chunks = self._chunks
        sp.chunk_offsets_ms = self._offsets
        sp.set_tokens(input=self._tokens_in, output=tokens_out)
        sp.set_output(text)
        if error is not None:
            sp.set_error(f"{type(error).__name__}: {error}")
        sp.close()
        if self._collector is not None:
            self._collector.save(sp)
        if self._is_new_trace:
            _on_trace_finished(sp.trace_id)

    def __del__(self) -> None:
        try:
            self._finalize()
        except Exception:
            pass


class _SyncStreamProxy(_StreamMixin):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._iter = iter(self._inner)

    def __iter__(self):
        return self

    def __next__(self) -> Any:
        try:
            chunk = next(self._iter)
        except StopIteration:
            self._finalize()
            raise
        except BaseException as exc:
            self._finalize(error=exc)
            raise
        self._on_chunk(chunk)
        return chunk

    def __enter__(self):
        enter = getattr(self._inner, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc: Any) -> Any:
        exit_ = getattr(self._inner, "__exit__", None)
        result = exit_(*exc) if exit_ is not None else None
        self._finalize(error=exc[1] if exc and exc[0] is not None else None)
        return result

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()
        self._finalize()


class _AsyncStreamProxy(_StreamMixin):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._aiter = self._inner.__aiter__()

    def __aiter__(self):
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._aiter.__anext__()
        except StopAsyncIteration:
            self._finalize()
            raise
        except BaseException as exc:
            self._finalize(error=exc)
            raise
        self._on_chunk(chunk)
        return chunk

    async def __aenter__(self):
        enter = getattr(self._inner, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        exit_ = getattr(self._inner, "__aexit__", None)
        result = await exit_(*exc) if exit_ is not None else None
        self._finalize(error=exc[1] if exc and exc[0] is not None else None)
        return result

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()
        self._finalize()


def _openai_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


def _anthropic_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
    )


def instrument_openai(
    client: Optional[Any] = None,
    agent: str = "openai",
    collector: Optional[TraceCollector] = None,
) -> bool:
    """Patch OpenAI chat completions to auto-trace calls.

    Pass an ``openai.OpenAI`` / ``openai.AsyncOpenAI`` instance to patch only
    that client; otherwise the SDK resource classes are patched globally.
    """
    resources: list[Any] = []
    if client is not None:
        try:
            resources.append(type(client.chat.completions))
        except AttributeError:
            return False
    else:
        try:
            from openai.resources.chat.completions import AsyncCompletions, Completions

            resources.extend([Completions, AsyncCompletions])
        except ImportError:
            return False

    patched = False
    for target in resources:
        patched = (
            _patch_method(
                target,
                "create",
                _llm_wrapper_factory(agent, collector, _openai_usage, _openai_chunk_text),
            )
            or patched
        )
    return patched


def instrument_anthropic(
    client: Optional[Any] = None,
    agent: str = "anthropic",
    collector: Optional[TraceCollector] = None,
) -> bool:
    """Patch Anthropic messages to auto-trace calls."""
    resources: list[Any] = []
    if client is not None:
        try:
            resources.append(type(client.messages))
        except AttributeError:
            return False
    else:
        try:
            from anthropic.resources.messages import AsyncMessages, Messages

            resources.extend([Messages, AsyncMessages])
        except ImportError:
            return False

    patched = False
    for target in resources:
        patched = (
            _patch_method(
                target,
                "create",
                _llm_wrapper_factory(agent, collector, _anthropic_usage, _anthropic_chunk_text),
            )
            or patched
        )
    return patched


def _register_langchain(collector: Optional[TraceCollector] = None) -> bool:
    """Register the LangChain handler as the global callback handler."""
    try:
        from langchain_core.callbacks import set_handler  # type: ignore
    except ImportError:
        return False
    handler = TraceForgeLangChainHandler(collector=collector)
    cm = set_handler(handler)
    try:
        cm.__enter__()
    except Exception:
        return False
    _langchain_cms.append(cm)
    return True


def _register_llamaindex(collector: Optional[TraceCollector] = None) -> bool:
    """Attach the LlamaIndex handler to the global Settings callback manager."""
    try:
        from llama_index.core import Settings  # type: ignore
        from llama_index.core.callbacks import CallbackManager  # type: ignore
    except ImportError:
        return False
    handler = TraceForgeLlamaIndexHandler(collector=collector)
    try:
        Settings.callback_manager = CallbackManager([handler])
    except Exception:
        return False
    return True


def instrument(
    collector: Optional[TraceCollector] = None,
    providers: Optional[list[str]] = None,
) -> dict[str, bool]:
    """Enable auto-instrumentation for the requested providers.

    ``providers`` defaults to the SDKs that can be monkey-patched
    (``openai``, ``anthropic``). Framework providers (``langchain``,
    ``llamaindex``) register a global callback handler when their SDK is
    installed.

    Returns a mapping of ``{provider: patched_or_not}``. Providers whose SDK
    is not installed return ``False`` and are skipped.
    """
    registrars = {
        "openai": instrument_openai,
        "anthropic": instrument_anthropic,
        "langchain": _register_langchain,
        "llamaindex": _register_llamaindex,
    }
    if providers is None:
        providers = ["openai", "anthropic"]
    return {name: registrars[name](collector=collector) for name in registrars if name in providers}


_langchain_cms: list[Any] = []


def _langchain_model(serialized: Any, kwargs: dict) -> Optional[str]:
    invocation = kwargs.get("invocation_params") or {}
    if isinstance(invocation, dict):
        model = invocation.get("model") or invocation.get("model_name")
    else:
        model = getattr(invocation, "model", None) or getattr(invocation, "model_name", None)
    if model:
        return model
    if isinstance(serialized, dict):
        return serialized.get("name") or (serialized.get("id")[-1] if serialized.get("id") else None)
    return getattr(serialized, "name", None)


class TraceForgeLangChainHandler:
    """Drop-in ``BaseCallbackHandler`` for LangChain / LangGraph LLM calls.

    Usage::

        from traceforge.auto import TraceForgeLangChainHandler
        handler = TraceForgeLangChainHandler()
        chain = ...  # pass callbacks=[handler] to the chain or LLM
    """

    raise_error = False

    def __init__(self, agent: str = "langchain", collector: Optional[TraceCollector] = None):
        self.agent = agent
        self.collector = collector
        self._active: dict[Any, tuple[Any, Any]] = {}

    def on_llm_start(self, serialized: Any, prompts: list[str], **kwargs: Any) -> None:
        model = _langchain_model(serialized, kwargs)
        cm = span(agent=self.agent, model=model, collector=self.collector)
        sp = cm.__enter__()
        sp.input = {"prompts": prompts}
        self._active[kwargs.get("run_id")] = (cm, sp)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        entry = self._active.pop(kwargs.get("run_id"), None)
        if entry is None:
            return
        cm, sp = entry
        text = ""
        generations = getattr(response, "generations", None)
        if generations:
            first = generations[0][0] if generations[0] else None
            if first is not None:
                text = getattr(first, "text", "") or ""
        sp.set_output(text)
        cm.__exit__(None, None, None)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        entry = self._active.pop(kwargs.get("run_id"), None)
        if entry is None:
            return
        cm, sp = entry
        sp.set_error(f"{type(error).__name__}: {error}")
        cm.__exit__(None, None, None)


class TraceForgeLlamaIndexHandler:
    """Drop-in callback handler for LlamaIndex LLM calls.

    Usage::

        from traceforge.auto import TraceForgeLlamaIndexHandler
        handler = TraceForgeLlamaIndexHandler()
        Settings.callback_manager = CallbackManager([handler])
    """

    def __init__(self, agent: str = "llamaindex", collector: Optional[TraceCollector] = None):
        self.agent = agent
        self.collector = collector
        self._active: dict[Any, tuple[Any, Any]] = {}

    def on_llm_start(self, event: Any, **kwargs: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        messages = payload.get("messages")
        serialized = payload.get("serialized")
        if isinstance(serialized, dict):
            model = serialized.get("model")
        else:
            model = getattr(serialized, "model", None)
        cm = span(agent=self.agent, model=model, collector=self.collector)
        sp = cm.__enter__()
        if messages is not None:
            sp.input = {"messages": [getattr(m, "content", None) for m in messages]}
        self._active[getattr(event, "id", None)] = (cm, sp)

    def on_llm_end(self, event: Any, **kwargs: Any) -> None:
        entry = self._active.pop(getattr(event, "id", None), None)
        if entry is None:
            return
        cm, sp = entry
        response = (getattr(event, "payload", {}) or {}).get("response")
        sp.set_output(getattr(response, "response", None) or getattr(response, "text", None) or str(response))
        cm.__exit__(None, None, None)

    def on_llm_error(self, event: Any, **kwargs: Any) -> None:
        entry = self._active.pop(getattr(event, "id", None), None)
        if entry is None:
            return
        cm, sp = entry
        exception = (getattr(event, "payload", {}) or {}).get("exception")
        if exception is None:
            exception = kwargs.get("exception") or kwargs.get("error") or Exception("llm error")
        sp.set_error(f"{type(exception).__name__}: {exception}")
        cm.__exit__(None, None, None)
