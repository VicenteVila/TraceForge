"""A/B comparison of prompts with runtime metrics and optional quality scoring.

Runs an instrumented function ``fn(prompt, sample)`` over every
(variant, sample) combination, collects the produced spans, and compares each
variant by latency, tokens, cost, error rate and (optionally) an LLM-judge or
reference-based quality score.

Examples::

    from traceforge.abtest import compare_prompts
    from traceforge.evals import openai_judge
    import traceforge

    @traceforge.trace(agent="writer")
    def answer(prompt, sample):
        return llm(prompt + sample)   # any LLM call, auto-instrumented

    result = compare_prompts(
        answer,
        {"concise": "Answer in 1 sentence.", "detailed": "Answer in 3 sentences."},
        samples=["What is AI?", "Explain entropy."],
        judge=openai_judge(),
    )
    print(result.winner, result.reason)
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .collector.memory import MemoryCollector
from .core import TraceCollector
from .decorator import set_default_collector
from .evals import evaluate_span


@dataclass
class VariantMetrics:
    name: str
    runs: int
    avg_duration_ms: float
    avg_tokens: float
    avg_cost_usd: float
    error_rate: float
    avg_eval_score: Optional[float] = None
    eval_pass_rate: Optional[float] = None


@dataclass
class ABResult:
    variants: list[VariantMetrics]
    winner: Optional[str]
    reason: str

    def __str__(self) -> str:
        lines = [f"winner: {self.winner} ({self.reason})"]
        for v in self.variants:
            lines.append(
                f"  {v.name}: {v.runs} runs | {v.avg_duration_ms:.0f}ms | "
                f"{v.avg_tokens:.0f} tok | ${v.avg_cost_usd:.4f} | "
                f"errors {v.error_rate:.0%}"
                + (f" | eval {v.avg_eval_score:.3f}" if v.avg_eval_score is not None else "")
            )
        return "\n".join(lines)


def compare_prompts(
    fn: Callable[[str, Any], Any],
    prompts: dict[str, str],
    samples: Optional[list[Any]] = None,
    *,
    collector: Optional[TraceCollector] = None,
    judge: Optional[Callable[[str], str]] = None,
    references: Optional[list[Any]] = None,
    threshold: float = 0.5,
) -> ABResult:
    """Compare prompt variants by running an instrumented function.

    ``fn`` must be trace-instrumented (e.g. ``@traceforge.trace``) and accept
    ``(prompt, sample)``. Each (variant, sample) run is captured into a fresh
    memory collector by default, or into ``collector`` if provided.
    ``references`` is an optional list aligned with ``samples`` used to score
    factuality per run.
    """
    if isinstance(prompts, str):
        prompts = {"prompt": prompts}
    samples = [None] if samples is None else list(samples)
    references = references or [None] * len(samples)

    variants: list[VariantMetrics] = []
    for name, prompt in prompts.items():
        agg = {"durations": [], "tokens": [], "costs": [], "errors": 0, "evals": [], "passes": 0, "runs": 0}
        for sample, reference in zip(samples, references):
            agg["runs"] += 1
            local: Optional[TraceCollector] = None
            if collector is None:
                local = MemoryCollector()
                set_default_collector(local)
                c = local
                before_ids: set[str] = set()
            else:
                c = collector
                before_ids = set(c.list_traces(limit=10000))

            failed = False
            try:
                fn(prompt, sample)
            except Exception:
                failed = True

            if failed:
                agg["errors"] += 1
                continue

            if collector is None:
                new_ids = c.list_traces(limit=10000)
            else:
                new_ids = [t for t in c.list_traces(limit=10000) if t not in before_ids]
            spans = [s for tid in new_ids for s in c.get_trace(tid)]
            if not spans:
                agg["errors"] += 1
                continue

            agg["durations"].append(sum(s.duration_ms for s in spans))
            agg["tokens"].append(sum(s.tokens_input + s.tokens_output for s in spans))
            agg["costs"].append(sum(s.cost_usd for s in spans))

            last = max(spans, key=lambda s: s.started_at)
            results = evaluate_span(last, judge=judge, span_reference=reference, threshold=threshold)
            if results:
                avg = sum(r.score for r in results) / len(results)
                agg["evals"].append(avg)
                agg["passes"] += sum(1 for r in results if r.passed)

        total = agg["runs"]
        eval_total = len(agg["evals"])
        variants.append(
            VariantMetrics(
                name=name,
                runs=total,
                avg_duration_ms=sum(agg["durations"]) / len(agg["durations"]) if agg["durations"] else 0.0,
                avg_tokens=sum(agg["tokens"]) / len(agg["tokens"]) if agg["tokens"] else 0.0,
                avg_cost_usd=sum(agg["costs"]) / len(agg["costs"]) if agg["costs"] else 0.0,
                error_rate=agg["errors"] / total if total else 0.0,
                avg_eval_score=sum(agg["evals"]) / eval_total if eval_total else None,
                eval_pass_rate=agg["passes"] / eval_total if eval_total else None,
            )
        )

    winner, reason = _pick_winner(variants)
    return ABResult(variants=variants, winner=winner, reason=reason)


def _pick_winner(variants: list[VariantMetrics]) -> tuple[Optional[str], str]:
    eligible = [v for v in variants if v.runs > 0 and v.error_rate < 1.0]
    if not eligible:
        return None, "no variant completed a run without errors"

    def key(v: VariantMetrics) -> tuple[Any, ...]:
        # best quality first, then cheapest, then fastest
        quality = v.avg_eval_score if v.avg_eval_score is not None else -1.0
        return (-quality, v.avg_cost_usd, v.avg_duration_ms)

    best = min(eligible, key=key)
    reason_parts = []
    if best.avg_eval_score is not None:
        reason_parts.append(f"eval {best.avg_eval_score:.3f}")
    reason_parts.append(f"${best.avg_cost_usd:.4f}/run")
    reason_parts.append(f"{best.avg_duration_ms:.0f}ms avg")
    reason_parts.append(f"{best.error_rate:.0%} errors")
    return best.name, ", ".join(reason_parts)


__all__ = ["ABResult", "VariantMetrics", "compare_prompts"]
